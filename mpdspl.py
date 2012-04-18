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

import codecs, cPickle, datetime, operator, optparse
import os, os.path, sqlite3, sys, re, textwrap, time

import mpd

DEFAULT_HOST = 'localhost'
DEFAULT_PORT = '6600'

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

KEYWORDS = {"ar"   : ("Artist", "Artist"),
            "al"   : ("Album", "Album"),
            "ti"   : ("Title", "Title"),
            "tn"   : ("Track", "Track number"),
            "ge"   : ("Genre", "Genre"),
            "ye"   : ("Date", "Track year"),
            "le"   : ("Time", "Track duration (in seconds)"),
            "fp"   : ("file", "File full path"),
            "fn"   : ("key", "File name"),
            "mt"   : ("mtime", "File modification time"),
            "ra"   : ("Rating", "Track rating"),
            "raar" : ("RatingAr", "Artist rating"),
            "raal" : ("RatingAl", "Album rating"),
            "rag"  : ("RatingGe", "Genre rating"),
            "pc"   : ("PlayCount", "Play Count") }

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
        attr = getattr(track, self.key.lower())
        
        matched = self.__match__(attr)

        if self.negate:
            matched = not matched
        return matched
    
class RegexRule(AbstractRule):
    """ Search according to a regex, for instance:
               contains foo                     -->   =/foo/
               contains bar, case-insensitive   -->   =/bar/i
               does not contain baz             -->   !/foo/ """
    
    OPERATORS = { '=' : re.search,
                  '!' : lambda *v: not re.search(*v) }
    FLAGS = { 'i' : re.IGNORECASE,
              'l' : re.LOCALE }
    
    def __init__(self, key, operator, delimiter, value, flags):
        AbstractRule.__init__(self, key, operator,
                              delimiter, value, flags)
        self.reFlags = 0
        for reFlag in self.flags:
            self.reFlags |= self.FLAGS[reFlag]

    def __match__(self, value):
        value = str(value)
        return self.getOperator()(self.value, value, self.reFlags)
        
class NumberRule(AbstractRule):
    """ Match according to a number comparison, for instance:
               greater or equal than 30         -->   >=#30#
               lesser than 80                   -->   <#80# """
    
    OPERATORS = { '=' : operator.eq,
                  '<' : operator.lt,
                  '>' : operator.gt,
                  '>=' : operator.ge,
                  '<=' : operator.ge }
    
    def __init__(self, key, operator, delimiter, value, flags):
        AbstractRule.__init__(self, key, operator,
                              delimiter, value, flags)
        self.number = float(value)
        
    def __match__(self, value):
        if not value:
            value = 0
        return self.getOperator()(float(value), self.number)
        
class TimeDeltaRule(AbstractRule):
    """ Match according to a timedelta, for instance:
               in the last 3 days   -->   <=%3days%
               before last month    -->   >%1month%
               3 years ago          -->   =%3years% """
    
    OPERATORS = { '=' : operator.eq,
                  '<' : operator.lt,
                  '>' : operator.gt,
                  '>=' : operator.ge,
                  '<=' : operator.le }
    
    TIME_DELTA_REGEX = r'(?P<number>\d+)\s*(?P<unit>[a-zA-Z]+)'

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

        self.value = datetime.timedelta(**{self.unit : self.number})
        self.now = datetime.datetime.now()

    def __match__(self, value):
        if not value:
            value = 0
        try:
            delta = self.now - datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
        except:
            delta = self.now - datetime.datetime.fromtimestamp(float(value))
        return self.getOperator()(delta, self.value)

class TimeStampRule(AbstractRule):
    """ Match according to a timestamp, for instance:
               before 2010-01-02            -->   <@2010-01-02@
               after  2009-12-20 (included) -->   >=@2009-12-20@
               on     2009-11-18            -->   =@2009-11-18@ """
    
    OPERATORS = { '=' : operator.eq,
                  '<' : operator.lt,
                  '>' : operator.gt,
                  '>=' : operator.ge,
                  '<=' : operator.le }
    
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
                          '@' : TimeStampRule,
                          '#' : NumberRule }

    @staticmethod
    def getRule(ruleString):
        m = re.match(r'(?P<key>\w+)(?P<operator>.+?)(?P<delimiter>[' +
                     ''.join(RuleFactory.DELIMITER_TO_RULE.keys()) +
                     r'])(?P<value>.+)(?P=delimiter)(?P<flags>\w+)?',
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
    def initStaticAttributes(playlistDir, cacheDir):
        Playlist.PLAYLIST_DIR = playlistDir
        Playlist.CACHE_DIR = cacheDir

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

        self.tracks.sort()
        self.setM3u()

    def setM3u(self):
        l = [ track.file for track in self.tracks ]
        self.m3u = '\n'.join(l)

    def getM3uPath(self):
        return os.path.join(self.PLAYLIST_DIR, self.name + ".m3u")

    def writeM3u(self):
        filePath = self.getM3uPath()
        print "Saving playlist '%s' to '%s'" % (playlist.name, filePath)
        open(filePath, 'w').write(self.m3u + '\n')

class PlaylistSet:
    def __init__(self, playlists):
        self.playlists = playlists

    def addMarshalled(self, name):
        if name in playlists.keys():
            raise CustomException("Cowardly refusing to create a new '%s' playlist since '%s' already exists." % (name, Playlist.getSaveFile(name)))
        playlists[name] = Playlist.load(name)

    def getPlaylists(self):
        return self.playlists.values()

class Track:
    def __init__(self, track = None):
        # first, create a track object with only empty attributes
        for key in KEYWORDS.values():
            setattr(self, key[0].lower(), "")

        # fill in with the optional parameter's attributes
        for key, value in track.iteritems():
            if isinstance(value, list):
                value = value[0]
            if key == 'last-modified':
                key = 'mtime'
            setattr(self, key.lower(), value)

    def __cmp__(self, t2):
        return cmp(self.artist + self.album + self.title,
                   t2.artist + t2.album + t2.title)

    def __repr__(self):
        return ("%(artist)s - %(album)s - %(track)s - %(title)s" % self.__dict__)

class MpdDB:
    CACHE_FILE = None # where to save marshalled DB
    
    def __init__(self, host, port,
                 stickerFile = None, mpdcronStatsFile = None):
        self.host = host
        self.port = port
        self.stickerFile = stickerFile
        self.mpdcronStatsFile = mpdcronStatsFile
        self.tracks = {}
        self.__parseDB()
        if mpdcronStatsFile:
            self.__parseMpdcronDB()            
        elif self.stickerFile:
            self.__parseStickerDB()

    @staticmethod
    def initStaticAttributes(cacheFile):
        MpdDB.CACHE_FILE = cacheFile

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

    def __parseDB(self):
        client = mpd.MPDClient()
        client.connect(self.host, self.port)
        client.iterate = True

        for track in client.listallinfo():
            if not 'file' in track:
                continue
            track = Track(track)
            self.tracks[track.file] = track

    def __parseStickerDB(self):
        conn = sqlite3.connect(self.stickerFile)

        curs = conn.cursor()

        curs.execute('SELECT * FROM sticker WHERE type=? and name=?',
                     ("song", "rating"))

        for row in curs:
            filePath = row[1]
            if filePath in self.tracks:
                self.tracks[filePath].rating = row[3]

    def __parseMpdcronDB(self):
        conn = sqlite3.connect(self.mpdcronStatsFile)

        curs = conn.cursor()

        curs.execute('''
SELECT song.uri, song.rating, artist.rating, album.rating, genre.rating, song.play_count
FROM song, artist, album, genre
WHERE song.artist = artist.name
AND song.album = album.name
AND song.genre = genre.name
AND song.rating + artist.rating + album.rating + genre.rating + song.play_count > 0''', ())

        for row in curs:
            filePath = row[0]
            if filePath in self.tracks:
                self.tracks[filePath].rating = row[1]
                self.tracks[filePath].ratingar = row[2]
                self.tracks[filePath].ratingal = row[3]
                self.tracks[filePath].ratingge = row[4]
                self.tracks[filePath].playcount = row[5]

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

def parseArgs(args):
    parser = optparse.OptionParser(formatter=IndentedHelpFormatterWithNL(),
                                   description="""Playlist ruleset:
        Each ruleset is made of several rules, separated by commas.
        Each rule is made of a keyword, an operator, a value to match
        surrounded by delimiters, and several optional flags influencing the
        match.
        There are """ + str(len(RuleFactory.DELIMITER_TO_RULE.keys())) + \
        """ types of rules, each defined by a specific delimiter:\n\n""" + \

        RuleFactory.help() + \

        """\n        These available keywords are:
""" + \

        '\n'.join([ "            " + k + "/" + v[0] + " : " + v[1].lower() for k, v in KEYWORDS.iteritems() ]) + \

        """

        For example, a rule for all tracks by 'Fred' or 'George', which have a
        title containing (case-insensitive) 'the' and 'and', which don't
        include the word 'when' (case-insensitive), and whose modification
        time was in the last 3 days would be written:

          ar=/(Fred|George)/ , ti=/(the.*and|and.*the)/i , ti!/when/i , mt<%3days%
          
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

    parser.add_option("-H", "--host", dest="host", help="Host MPD runs on",
                      default=DEFAULT_HOST, metavar="HOST")

    parser.add_option("-P", "--port", dest="port", help="Port MPD runs on",
                      default=DEFAULT_PORT, metavar="PORT")

    parser.add_option("-s", "--sticker-file", dest="stickerFile",
                      help="Location of the MPD sticker file (holding ratings)",
                      metavar="FILE")

    parser.add_option("-m", "--mpdcron-stats-file", dest="mpdcronStatsFile",
                      help="Location of the mpdcron stats file (holding ratings and other info)",
                      default=None,
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
                      action="store", default='',
                      help="Only print the final track list to STDOUT")

    options, args = parser.parse_args(args)

    if getattr(options, "mpdcronStatsFile") and getattr(options, "stickerFile"):
        print "Can't use -s and -m at the same time, as they both provide ratings."
        sys.exit(2)

    # we'll use dataDir=None to indicate we want simpleOutput
    if options.simpleOutput:
        options.dataDir = None

    # go from ((name,rule),(name1,rule1),...) to {name:rule,name1:rule1,...}
    playlists = {}
    for name, ruleSet in options.playlists:
        playlists[name] = Playlist(name, ruleSet)
    options.playlists = playlists

    if options.simpleOutput:
        options.playlists['stdout'] = Playlist('stdout', options.simpleOutput)

    return options.forceUpdate, options.cacheFile, options.dataDir, \
           options.host, options.port, options.stickerFile, \
           options.mpdcronStatsFile, \
           options.playlistDirectory, options.playlists

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

def savegubbage(data, path):
    if not os.path.isdir(os.path.dirname(path)):
        os.mkdir(os.path.dirname(path))
    cPickle.dump(data, open(path, "wb"))

def loadgubbage(path):
    return cPickle.load(open(path, "rb"))

if __name__ == '__main__':
   try:
      forceUpdate, cacheFile, dataDir, \
                   host, port, stickerFile, \
                   mpdcronStatsFile, \
                   playlistDir, playlists = parseArgs(sys.argv[1:])

      MpdDB.initStaticAttributes(cacheFile)
      Playlist.initStaticAttributes(playlistDir, dataDir)

      playlistSet = PlaylistSet(playlists)

      if forceUpdate:
          if dataDir:
              print "Updating database cache..."

          if not os.path.isdir(os.path.dirname(cacheFile)):
              os.mkdir(os.path.dirname(cacheFile))

          # create the MPD DB object
          if mpdcronStatsFile:
              mpdDB = MpdDB(host, port, mpdcronStatsFile=mpdcronStatsFile)
          else:
              mpdDB = MpdDB(host, port, stickerFile=stickerFile)
              
          mpdDB.save() # save to file
      else: # we have a valid cache file, use it
          if dataDir:
              print "Loading database cache..."
          mpdDB = MpdDB.load()

      if dataDir: # add pre-existing playlists to our list
          for name in os.listdir(Playlist.CACHE_DIR):
              playlistSet.addMarshalled(name)

      for playlist in playlistSet.getPlaylists():
          playlist.findMatchingTracks(mpdDB)

          if not dataDir: # stdout
              if playlist.m3u:
                  print playlist.m3u
          else: # write to .m3u & save
              playlist.writeM3u()
              playlist.save()
   except CustomException, e:
       print e.message
       sys.exit(2)
