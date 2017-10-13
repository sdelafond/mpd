import optparse, os, os.path, re, sys

import mpdutils

# mpd host, port
MPD_CONNECTION = ('localhost', 6600)

# mpd root directory
MP3_ROOT = os.path.expanduser('/data/mp3s/done')

# Covers dir
COVERS_DIR = os.path.expanduser('~/.covers/')

# options parser
parser = optparse.OptionParser(usage = "Usage: %prog [options] <playlist-name>")
parser.add_option("-m", "--mount-point", dest="mountPoint",
                  metavar="MOUNT_POINT", default="/mnt",
                  help="Mount point for the android SD card")

options, playlists = parser.parse_args(sys.argv[1:])

# FIXME: temp file for m3u so it works with adb push too

def main():
  filenames = []

  for playlist in playlists:
    print "Playlist: %s" % playlist

    androidDir = "%s/mp3s/%s" % (options.mountPoint, playlist)
    tmpDir = "/tmp/mp3s/%s" % playlist

    if not os.path.isdir(androidDir):
      os.makedirs(androidDir)
    os.system("rm -fr '%s'" % tmpDir)
    os.makedirs(tmpDir)

    plFname = "%s/000-%s.m3u" % (androidDir, playlist)
    plFH = open(plFname, 'w')

    filenames = mpdutils.get_filenames(playlist, MPD_CONNECTION, MP3_ROOT)
    for f in filenames:
      basename = os.path.basename(f)
      destname = re.sub(r'[\\/:\*\?\"\<\>\|]', '_', "%s - %s" % (os.path.basename(os.path.dirname(f)),
                                                                 basename))
      print "  %s -> %s" % (basename, destname)
      try:
        os.symlink(f, "%s/%s" % (tmpDir, destname))
        plFH.write(destname + "\n")
      except Exception as e:
        print e.message

    plFH.close()

    syncCmd = "rsync -aLP --no-o --no-p --no-g --modify-window 1 '%s/' '%s/'" % (tmpDir, androidDir)
#    syncCmd = "cp -rL '%s/*' '%s'" % (tmpDir, androidDir)
    print syncCmd

    os.system(syncCmd)

if __name__ == '__main__':
  main()
