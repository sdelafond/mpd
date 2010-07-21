import mpd, os, sys
import mpdipod, mpdutils

# iPod mount point (make sure it's properly mounted)
MOUNT_POINT = '/media/usb0'

# mpd host, port
MPD_CONNECTION = ('localhost', 6602)

# mpd root directory
MP3_ROOT = os.path.expanduser('/data/mp3s/done')

# Covers dir
COVERS_DIR = os.path.expanduser('~/.covers/')

def sync(ipod, playlists):
    for mpd_playlist, ipod_playlist in playlists:
        tracks = [ ipod.track_factory(filename)
                   for filename in mpdutils.get_filenames(mpd_playlist) ]
        if not ipod.check_freespace(tracks):
            raise FreeSpaceException("Not enough free space!")
        ipod.sync_playlist(ipod_playlist, tracks)
    return True

def main():
    playlists = []
    for pl in sys.argv[1:]:
        playlists.append((pl, pl))

    ipod = mpdipod.iPod(MOUNT_POINT)
    sync(ipod, playlists)
    ipod.close()

if __name__ == '__main__':
     main()
