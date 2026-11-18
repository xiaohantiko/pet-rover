import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from media import observe


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ObservationTest(unittest.TestCase):
    def test_pattern_to_video(self):
        process = subprocess.Popen([sys.executable,'-u','camera_preview.py','--port','0','--source','pattern'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            for line in process.stdout:
                if '#key=' in line:
                    break
            else:
                self.fail('pattern service failed to start')
            address, token = line.strip().split('/#key=')
            port = address.rsplit(':', 1)[1]
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'sample.mp4'
                with patch('sys.argv',['observe',str(target),'--port',port,'--seconds','3']), patch('getpass.getpass',return_value=token), patch('sys.stdout',new_callable=io.StringIO):
                    observe.main()
                result = subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=nb_frames','-of','csv=p=0',str(target)], text=True)
                self.assertEqual(result.strip(), '12')
        finally:
            process.terminate()
            process.communicate(timeout=5)
