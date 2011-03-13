import os, os.path, sys
import mpdutils

# mpd host, port
MPD_CONNECTION = ('localhost', 6602)

# mpd root directory
MP3_ROOT = os.path.expanduser('/data/mp3s/done')

# Covers dir
COVERS_DIR = os.path.expanduser('~/.covers/')

def main():
  playlists = sys.argv[1:]
  filenames = []

  for playlist in playlists:
    os.system('adb shell mkdir "/sdcard/mp3s/%s"' % (playlist,))
    filenames = mpdutils.get_filenames(playlist, MPD_CONNECTION, MP3_ROOT)
    for f in filenames:
      print f
      os.system('adb push "%s" "/sdcard/mp3s/%s/%s"' % (f, playlist,
                                                        os.path.basename(f)))

if __name__ == '__main__':
  main()
