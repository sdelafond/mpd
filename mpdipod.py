import gpod, mpd, os

# iPod mount point (make sure it's properly mounted)
MOUNT_POINT = '/media/usb0'

# Safety factor, in gigabytes
SIZE_FUDGE = 0.4

# mpd host, port
MPD_CONNECTION = ('localhost', 6602)

# mpd root directory
MP3_ROOT = os.path.expanduser('/data/mp3s/done')

# Playlists (mpd_name, ipod_name)
#PLAYLISTS = [('Recent & Not iPod', 'Test'), ]
#PLAYLISTS = [('Audrey Mom', 'Test'), ]
PLAYLISTS = [('run', 'run'), ]

# Covers dir
COVERS_DIR = os.path.expanduser('~/.covers/')

# Code
def ipod_capacity(mountpoint = MOUNT_POINT):
    if not os.path.exists(mountpoint):
        raise ValueError("Mount point does not exist")
    device = gpod.itdb_device_new()
    gpod.itdb_device_set_mountpoint(device, mountpoint)
    info = gpod.itdb_device_get_ipod_info(device)
    print "Capacity: %i" % int((info.capacity - SIZE_FUDGE) * 1024 * 1024 * 1024)
    return int((info.capacity - SIZE_FUDGE) * 1024 * 1024 * 1024)

def compare_tracks(a, b):
    return ( #a['size'] == b['size'] and
            a['title'] == b['title'] and
            a['artist'] == b['artist'] and
            a['album'] == b['album'])

class FreeSpaceException(Exception): pass

class iPod(object):
    def __init__(self, path = MOUNT_POINT):
        self.path = path
        self.db = gpod.Database(self.path)

    # simplistic, but OK
    def used_space(self):
        size = 0
        for track in self.db.Master:
            size += track['size']
        for track in self.db.Podcasts:
            size += track['size']
        print "Used: %i" % size
        return size

    def free_space(self):
          return ipod_capacity(self.path) - self.used_space()

    def sync_playlist(self, name, tracks):
        for playlist in self.db.Playlists:
            print "found: %s" % playlist.name
            if playlist.name == name:
                for i, track in enumerate(tracks):
                    if i >= len(playlist):
                        playlist.add(track)
                        continue
                    if not compare_tracks(track, playlist[i]):
                        to_del = playlist[i]
                        playlist.remove(to_del)
                        self.db.Master.remove(to_del)
                        playlist.add(track, pos = i)
                if len(playlist) - 1 > i:
                    for track in playlist[i + 1:]:
                        playlist.remove(track)
                        self.db.Master.remove(track)
                return True
        playlist = self.db.new_Playlist(title = name)
        for track in tracks:
            print "about to add: %s" % track
            playlist.add(track)
        return True

    def check_freespace(self, tracks):
        size = sum([ track['size'] for track in tracks ])
        return size < self.free_space()

    def close(self):
        self.db.copy_delayed_files()
        self.db.close()

    def track_factory(self, filename):
        print filename
        _track = gpod.Track(filename)

        debug = False
        if filename.find('Mama') > 0:
            debug = True
            print _track

        for track in self.db.Master:
            if debug and track['title'].find('Mama') > 0:
                print track
                print track['size']
                print _track['size']
            if compare_tracks(_track, track):
                print "Same file: %s" % filename
                return track
        print "New file: %s" % filename
        t = self.db.new_Track(filename = filename)
        cover = os.path.join(COVERS_DIR, t['artist'],
                             "%s.jpg" % (t['album'], ))
        if os.path.isfile(cover):
            print "Setting cover for %s" % (filename,)
            t.set_coverart_from_file(cover)
        return t

# mpd code (move to different file when it gets too complex)

def get_filenames(mpd_playlist):
    client = mpd.MPDClient()
    client.connect(*MPD_CONNECTION)
    return [ os.path.join(MP3_ROOT, filename)
             for filename in client.listplaylist(mpd_playlist) ]

def sync(ipod, playlists):
    for mpd_playlist, ipod_playlist in playlists:
        tracks = [ ipod.track_factory(filename)
                   for filename in get_filenames(mpd_playlist) ]
        if not ipod.check_freespace(tracks):
            raise FreeSpaceException("Not enough free space!")
        ipod.sync_playlist(ipod_playlist, tracks)
    return True

def main():
    ipod = iPod(MOUNT_POINT)
    sync(ipod, PLAYLISTS)
    ipod.close()

if __name__ == '__main__':
     main()
