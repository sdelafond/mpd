import mpd, os

def get_filenames(mpd_playlist, mpd_connection, mp3_root):
    client = mpd.MPDClient()
    client.connect(*mpd_connection)
    return [ os.path.join(mp3_root, filename)
             for filename in client.listplaylist(mpd_playlist) ]
