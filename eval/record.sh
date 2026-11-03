#!/bin/zsh
# Records your mic to sessions/my_take.wav (16 kHz mono). Stop with Ctrl+C.
# Pick another mic: MIC="JBL TUNE570BT" make record   (list: ffmpeg -f avfoundation -list_devices true -i "")
cd "${0:A:h}/.."
mkdir -p sessions
MIC="${MIC:-MacBook Pro Microphone}"
echo "Recording from: $MIC  ->  sessions/my_take.wav   (Ctrl+C to stop)"
ffmpeg -hide_banner -loglevel error -y -f avfoundation -i ":$MIC" -ac 1 -ar 16000 sessions/my_take.wav
echo "saved sessions/my_take.wav"
