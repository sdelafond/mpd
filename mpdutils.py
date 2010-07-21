import mpd

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

