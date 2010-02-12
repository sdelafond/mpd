#! /usr/bin/env python
#
# A script to create smart playlists, with a variety of criteria, out of an
# MPD database.
#
# Authors:
#   Sebastien Delafond <sdelafond@gmail.com>
#   original implementation by Michael Walker <mike@barrucadu.co.uk>
#
# This code is licensed under the GPL v3, or any later version at your choice.

import cPickle, datetime, operator, optparse
import os, os.path, sqlite3, sys, re, textwrap, time

DEFAULT_MDP_CONFIG_FILE = "/etc/mpd.conf"

# There is an environmental variable XDG_CACHE_HOME which specifies where to
# save cache files. However, if not set, a default of ~/.cache should be used.
DEFAULT_CACHE_FILE = os.environ.get('XDG_CACHE_HOME',
                                    os.path.join(os.environ['HOME'], ".cache"))
DEFAULT_CACHE_FILE = os.path.expanduser(os.path.join(DEFAULT_CACHE_FILE,
                                                     "mpdspl/mpddb.cache"))

# $XDG_DATA_HOME specifies where to save data files, in our case a record of
# playlists which have been created. If unset a default of ~/.local/share
# should be used.
DEFAULT_DATA_DIR = os.environ.get('XDG_DATA_HOME',
                                  os.path.join(os.environ['HOME'], ".local/share/"))
DEFAULT_DATA_DIR = os.path.expanduser(os.path.join(DEFAULT_DATA_DIR,
                                                   "mpdspl"))

KEYWORDS = {"ar" : ("Artist", "Artist"),
            "al" : ("Album", "Album"),
            "ti" : ("Title", "Title"),
            "tn" : ("Track", "Track number"),
            "ge" : ("Genre", "Genre"),
            "ye" : ("Date", "Track year"),
            "le" : ("Time", "Track duration (in seconds)"),
            "fp" : ("file", "File full path"),
            "fn" : ("key", "File name"),
            "mt" : ("mtime", "File modification time"),
            "ra" : ("Rating", "Track rating") }

class CustomException(Exception):
    pass

class AbstractRule:
    def __init__(self, key, operator, delimiter, value, flags):
        if key.lower() in KEYWORDS:
            self.key = KEYWORDS[key.lower()][0]
        elif key.lower() in [ v[0].lower() for v in KEYWORDS.values() ]:
            self.key = key.lower()
        else:
            raise CustomException("A track has no attribute '%s'" % (key,))
        
        self.operator = operator
        self.delimiter = delimiter
        self.value = value
        if flags:
            self.flags = tuple(flags)
        else:
            self.flags = ()
        self.negate = 'n' in self.flags

    def __repr__(self):
        return "%(key)s%(operator)s%(delimiter)s%(value)s%(delimiter)s flags=%(flags)s" % self.__dict__

    def getOperator(self):
        return self.OPERATORS[self.operator]
    
    def match(self, track):
        matched = self.__match__(getattr(track, self.key.lower()))

        if self.negate:
            matched = not matched
        return matched
    
class RegexRule(AbstractRule):
    """ Search according to a regex, for instance:
               contains foo                     -->   =/foo/
               contains bar, case-insensitive   -->   =/bar/i """
    
    OPERATORS = { '=' : re.search }
    FLAGS = { 'i' : re.IGNORECASE,
              'l' : re.LOCALE }
    
    def __init__(self, key, operator, delimiter, value, flags):
        AbstractRule.__init__(self, key, operator,
                              delimiter, value, flags)
        self.reFlags = 0
        for reFlag in self.flags:
            self.reFlags |= self.FLAGS[reFlag]

    def __match__(self, value):
        return self.getOperator()(self.value, value, self.reFlags)
        
class TimeDeltaRule(AbstractRule):
    """ Match according to a timedelta, for instance:
               in the last 3 days   -->   <%3days%
               before last month    -->   >%1month%
               3 years ago          -->   =%3years% """
    
    OPERATORS = { '=' : operator.eq,
                  '<' : operator.le,
                  '>' : operator.ge }
    
    TIME_DELTA_REGEX = r'(?P<number>\d+)\s*(?P<unit>\w+)'

    def __init__(self, key, operator, delimiter, value, flags):
        AbstractRule.__init__(self, key, operator,
                              delimiter, value, flags)
        
        m = re.match(self.TIME_DELTA_REGEX, self.value)
        if not m:
            raise CustomException("Could not parse duration")
        d = m.groupdict()
        self.number = int(d['number'])
        self.unit = d['unit'].lower()
        if not self.unit.endswith('s'):
            self.unit += 's'

        self.timeDelta = datetime.timedelta(**{self.unit : self.number})
        self.value = self.timeDelta.seconds

    def __match__(self, value):
        return self.getOperator()(int(value), self.value)

class TimeStampRule(AbstractRule):
    """ Match according to a timestamp, for instance:
               before 2010-01-02   -->   <@2010-01-02@
               after  2009-12-20   -->   >@2009-12-20@
               on     2009-11-18   -->   =@2009-11-18@ """
    
    OPERATORS = { '=' : operator.eq,
                  '<' : operator.le,
                  '>' : operator.ge }
    
    TIME_STAMP_FORMAT = '%Y-%m-%d'

    def __init__(self, key, operator, delimiter, value, flags):
        AbstractRule.__init__(self, key, operator,
                              delimiter, value, flags)
        
        ts = time.strptime(self.value, self.TIME_STAMP_FORMAT)
        self.value = time.mktime(ts)

    def __match__(self, value):
        # round down to the precision of TIME_STAMP_FORMAT before comparing
        value = time.gmtime(float(value)) # in seconds since epoch
        value = time.strftime(self.TIME_STAMP_FORMAT, value)        
        value = time.mktime(time.strptime(value, self.TIME_STAMP_FORMAT))
        return self.getOperator()(value, self.value)

class RuleFactory:
    DELIMITER_TO_RULE = { '/' : RegexRule,
                          '%' : TimeDeltaRule,
                          '@' : TimeStampRule }

    @staticmethod
    def getRule(ruleString):
        m = re.match(r'(?P<key>\w+)(?P<operator>.)(?P<delimiter>[' +
                     ''.join(RuleFactory.DELIMITER_TO_RULE.keys()) +
                     r'])(?P<value>.+)\3(?P<flags>\w+)?',
                     ruleString)
        if not m:
            raise CustomException("Could not parse rule '%s'" % (ruleString,))

        d = m .groupdict()
        ruleClass = RuleFactory.DELIMITER_TO_RULE[d['delimiter']]
        return ruleClass(**d)

    @staticmethod
    def help():
        s = ""
        for d, r in RuleFactory.DELIMITER_TO_RULE.iteritems():
            s += "          '%s' -> %s\n" % (d, r.__doc__)
        return s

class Playlist:
    REGEX = re.compile(r'\s*,\s*') # how we split rules in a ruleset
    PLAYLIST_DIR = None # where to save m3u files
    CACHE_DIR = None # where to save marshalled playlists
    
    def __init__(self, name, ruleString):
        self.name = name
        self.rules = [ RuleFactory.getRule(r)
                       for r in self.REGEX.split(ruleString) ]
        self.tracks = [] # tracks matching the rules; empty for now

    @staticmethod
    def load(name):
        obj = loadgubbage(Playlist.getSaveFile(name))
        try:
            assert isinstance(obj, Playlist)
        except:
            raise CustomException("Restoring old playlists won't work, please rm '%s'." % (playlistfile,))

        return obj

    def save(self):
        savegubbage(self, Playlist.getSaveFile(self.name))

    @staticmethod
    def getSaveFile(name):
        return os.path.join(Playlist.CACHE_DIR, name)

    def findMatchingTracks(self, mpdDB):
        self.tracks = []
    
        for track in mpdDB.getTracks():
            toAdd = True
            for rule in self.rules:
                if not rule.match(track): # Add the track if appropriate
                    toAdd = False
                    break

            if toAdd:
                self.tracks.append(track)

        self.setM3u()

    def setM3u(self):
        self.m3u = '\n'.join([ track.file for track in self.tracks ]) + '\n'

    def getM3uPath(self):
        return os.path.join(self.PLAYLIST_DIR, self.name + ".m3u")

    def writeM3u(self):
        filePath = self.getM3uPath()
        print "Saving playlist '%s' to '%s'" % (playlist.name, filePath)
        open(filePath, 'w').write(self.m3u)

class PlaylistSet:
    def __init__(self, playlists):
        self.playlists = playlists

    def addMarshalled(self, name):
        if name in playlists.keys():
            raise CustomException("Cowardly refusing to create a new '%s' playlist when '%s' already exists." % (name, Playlist.getSaveFile(name)))
        playlists[name] = Playlist.load(name)

    def getPlaylists(self):
        return self.playlists.values()

class Track:
    def __init__(self):
        # create a track object with only empty attributes
        for key in KEYWORDS.values():
            setattr(self, key[0].lower(), "")

class MpdDB:
    CACHE_FILE = None # where to save marshalled DB
    
    def __init__(self, dbFile, stickerFile = None):
        self.dbFile = dbFile
        self.stickerFile = stickerFile
        self.tracks = {}
        self.__parseDB()
        self.__parseStickerDB()        

    @staticmethod
    def load():
        obj = loadgubbage(MpdDB.CACHE_FILE)
        try:
            assert isinstance(obj, MpdDB)
            tracks = obj.getTracks()
            if len(tracks) > 1:
                assert isinstance(tracks[-1], Track)
        except:
            raise CustomException("Restoring from old cache won't work, please use -f.")

        return obj

    def save(self):
        savegubbage(self, MpdDB.CACHE_FILE)

    @staticmethod
    def needUpdate(dbFile, stickerFile):
        return (not os.path.isfile(MpdDB.CACHE_FILE) \
                or os.path.getmtime(dbFile) > os.path.getmtime(MpdDB.CACHE_FILE) \
                or (os.path.isfile(stickerFile) \
                    and os.path.getmtime(stickerFile) > os.path.getmtime(MpdDB.CACHE_FILE)))
        
    def __parseDB(self):
        parsing = False

        track = None
        for line in open(self.dbFile, "r"):
            line = line.strip()

            if line == "songList begin": # enter parsing mode
                parsing = True
                continue
            if line == "songList end": # exit parsing mode
                parsing = False
                continue

            if parsing:
                if line.startswith("key: "):
                    if track is not None: # save the previous one
                        self.tracks[track.file] = track
                    track = Track() # create a new one

                key, value = line.split(": ", 1)
                setattr(track, key.lower(), value)

    def __parseStickerDB(self):
        conn = sqlite3.connect(self.stickerFile)

        curs = conn.cursor()

        curs.execute('SELECT * FROM sticker WHERE type=? and name=?',
                     ("song", "rating"))

        for row in curs:
            filePath = row[1]
            if filePath in self.tracks:
                self.tracks[filePath].rating = row[3]

    def getTracks(self):
        return self.tracks.values()

class IndentedHelpFormatterWithNL(optparse.IndentedHelpFormatter):
    """ So optparse doesn't mangle our help description. """
    def format_description(self, description):
        if not description: return ""
        desc_width = self.width - self.current_indent
        indent = " "*self.current_indent
        bits = description.split('\n')
        formatted_bits = [ textwrap.fill(bit,
                                         desc_width,
                                         initial_indent=indent,
                                         subsequent_indent=indent)
                           for bit in bits]
        result = "\n".join(formatted_bits) + "\n"
        return result 

def parseargs(args):
    parser = optparse.OptionParser(formatter=IndentedHelpFormatterWithNL(),
                                   description="""Playlist ruleset:
        Each ruleset is made of several rules, separated by commas.
        Each rule is made of a keyword, an operator, a value to match
        surrounded by delimiters, and several optional flags influencing the
        match.
        There are """ + str(len(RuleFactory.DELIMITER_TO_RULE.keys())) + \
        """ types of rules, each defined by a specific delimiter:\n\n""" + \

        RuleFactory.help() + \

        """        These available keywords are:
""" + \

        '\n'.join([ "            " + k + "/" + v[0] + " : " + v[1].lower() for k, v in KEYWORDS.iteritems() ]) + \

        """

        For example, a rule for all tracks by 'Fred' or 'George', which have a
        title containing (case-insensitive) 'the' and 'and', which don't
        include the word 'when' (case-insensitive), and whose modification
        time was in the last 3 days would be written:

          ar=/(Fred|George)/ ti=/(the.*and|and.*the)/i ti=/when/i mt<%3days%
          
    Notes:
        Paths specified in the MPD config file containing a '~' will have the
        '~'s replaced by the user MPD runs as..""")

    parser.add_option("-f", "--force-update", dest="forceUpdate",
                      action="store_true", default=False,
                      help="Force an update of the cache file and any playlists")

    parser.add_option("-C", "--cache-file", dest="cacheFile",
                      default=DEFAULT_CACHE_FILE,
                      help="Location of the cache file", metavar="FILE")

    parser.add_option("-D", "--data-dir", dest="dataDir",
                      default=DEFAULT_DATA_DIR,
                      help="Location of the data directory (where we save playlist info)",
                      metavar="DIR")

    parser.add_option("-d", "--database-file", dest="dbFile", 
                      help="Location of the MPD database file",
                      metavar="FILE")

    parser.add_option("-s", "--sticker-file", dest="stickerFile",
                      help="Location of the MPD sticker file( holding ratings)",
                      metavar="FILE")

    parser.add_option("-c", "--config-file", dest="configFile",
                      default=DEFAULT_MDP_CONFIG_FILE,
                      help="Location of the MPD config file",
                      metavar="FILE")

    parser.add_option("-p", "--playlist-dir", dest="playlistDirectory",
                      help="Location of the MPD playlist directory",
                      metavar="DIR")

    parser.add_option("-u", "--user", dest="mpdUser",
                      help="User MPD runs as", metavar="USER")

    parser.add_option("-n", "--new-playlist", dest="playlists",
                      action="append", default=[], nargs=2,
                      help="Create a new playlist",
                      metavar="NAME 'RULESET'")

    parser.add_option("-o", "--output-only", dest="simpleOutput",
                      action="store_true", default=False,
                      help="Only print the final track list to STDOUT")

    options, args = parser.parse_args(args)

    # we'll use dataDir=None to indicate we want simpleOutput
    if options.simpleOutput:
        options.dataDir = None

    # go from ((name,rule),(name1,rule1),...) to {name:rule,name1:rule1,...}
    playlists = {}
    for name, ruleSet in options.playlists:
        playlists[name] = Playlist(name, ruleSet)
    options.playlists = playlists

    configDict = parsempdconf(os.path.expanduser(options.configFile),
                              options.mpdUser)

    # CL arguments take precedence over config file settings
    for key in configDict:
        if key in dir(options) and getattr(options, key):
            configDict[key] = getattr(options, key)

    return options.forceUpdate, options.cacheFile, options.dataDir, \
           configDict['dbFile'], configDict['stickerFile'], \
           configDict['playlistDirectory'], options.playlists

def _underscoreToCamelCase(s):
    tokens = s.split('_')
    s = tokens[0]
    for token in tokens[1:]:
        s += token.capitalize()
    return s
    
# Grabbing stuff from the MPD config, a very important step
def parsempdconf(configFile, user = None):
    configDict = {}
    for line in open(configFile, "r"):
        line = line.strip()
        if line and not re.search(r'[#{}]', line):
            key, value = re.split(r'\s+', line, 1)

            key = _underscoreToCamelCase(key)

            value = re.sub(r'(^"|"$)', '', value)

            # account for ~/ in mpd.conf
            if value == '~' or value.count('~/') > 0: # FIXME: others ?
                if user:
                    value = value.replace('~', user)
                else:
                    value = os.path.expanduser(value)
                    
            configDict[key] = value

    return configDict

# Save some random gubbage to a file
def savegubbage(data, path):
    if not os.path.isdir(os.path.dirname(path)):
        os.mkdir(os.path.dirname(path))

    cPickle.dump(data, open(path, "wb"))

    # We might be running as someone other than the user, so make the file writable
    os.chmod(path, 438)

def loadgubbage(path):
    return cPickle.load(open(path, "rb"))

try:
   forceUpdate, cacheFile, dataDir, dbFile, stickerFile, playlistDir, playlists = parseargs(sys.argv[1:])

   MpdDB.CACHE_FILE = cacheFile

   Playlist.PLAYLIST_DIR = playlistDir
   Playlist.CACHE_DIR = dataDir

   playlistSet = PlaylistSet(playlists)

   # Check that the database is actually there before attempting to do stuff with it.
   if not os.path.isfile(dbFile):
       raise CustomException("The database file '%s' could not be found.\n" % (dbFile,))

   # If no cache file, or one of MPD's DBs is more recent than it, re-parse the DB
   if forceUpdate or MpdDB.needUpdate(dbFile, stickerFile):
       if dataDir:
           print "Updating database cache..."

       if not os.path.isdir(os.path.dirname(cacheFile)):
           os.mkdir(os.path.dirname(cacheFile))

       mpdDB = MpdDB(dbFile, stickerFile) # MPD DB object
       mpdDB.save() # save to file
   else: # we have a valid cache file, use it
       if dataDir:
           print "Loading database cache..."
       mpdDB = MpdDB.load()

   if dataDir: # add pre-existing playlists to our list
       for name in os.listdir(Playlist.CACHE_DIR):
           playlistSet.addMarshalled(name)

   for playlist in playlistSet.getPlaylists(): # now generate all the playlists
       playlist.findMatchingTracks(mpdDB)

       if not dataDir: # stdout
           for track in playlist.tracks:
               print track.file
       else: # write to .m3u & save
           playlist.writeM3u()
           playlist.save()
except CustomException, e:
    print e.message
    sys.exit(2)
