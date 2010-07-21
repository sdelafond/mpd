import gpod, os

def compare_tracks(a, b):
    return ( #a['size'] == b['size'] and
            a['title'] == b['title'] and
            a['artist'] == b['artist'] and
            a['album'] == b['album'])

class FreeSpaceException(Exception): pass

class iPod(object):

    SIZE_FUDGE = 0.4 # safety factor, in gigabytes

    def __init__(self, path):
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

    def ipod_capacity(self):
        if not os.path.exists(self.path):
            raise ValueError("Mount point does not exist")
        device = gpod.itdb_device_new()
        gpod.itdb_device_set_mountpoint(device, mountpoint)
        info = gpod.itdb_device_get_ipod_info(device)
        print "Capacity: %i" % int((info.capacity - iPod.SIZE_FUDGE) * 1024 * 1024 * 1024)
        return int((info.capacity - iPod.SIZE_FUDGE) * 1024 * 1024 * 1024)

    def free_space(self):
          return self.ipod_capacity() - self.used_space()

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
                        try:
                            self.db.Master.remove(to_del)
                        except:
                            print "** Problem removing %s" % (track,)
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
        try:
            cover = os.path.join(COVERS_DIR, t['artist'],
                             "%s.jpg" % (t['album'], ))
            if os.path.isfile(cover):
                print "Setting cover for %s" % (filename,)
                t.set_coverart_from_file(cover)
        except:
            pass

        return t
