#!/usr/bin/env python3
"""
Verify a finished CRP audio mix. Exits non-zero on any failure.

Usage:
    verify_mix.py MIXED_MP3 EXPECTED_SECONDS VOICE_DELAY_MS VOICE_LEN OUTRO_DELAY_MS

Three checks, each earning its place from something that actually went wrong:

1. DURATION against the PLAN, not against itself.
   Episode 2 shipped to the client at 9 minutes instead of 27. The cause was
   stray ID3 headers left mid-stream by a naive concat. ffprobe scans every
   frame, so it reported the true 27 minutes and the fault sailed through
   verification. Comparing a file's duration to its own metadata proves nothing.
   This compares it to what the timeline arithmetic predicted.

2. NO ID3 HEADER MID-STREAM.
   The structural check that would have caught the above directly. A strict
   decoder - Spotify, most podcast apps - reads the first header, hits the next
   ID3 tag as garbage, and stops. ffmpeg is far more forgiving, which is exactly
   why a tolerant decoder cannot be used to clear a media file.

3. THE BEDS ARE AUDIBLE.
   The real failure mode for a mix is not a crash. It is a mix that succeeds and
   sounds like plain voice, because a bed was missing, silent, or resampled into
   nothing by a format mismatch. Measuring level in three windows catches that.
"""
import re
import subprocess
import sys

FRONT_MATTER_BYTES = 4096   # an ID3v2 tag past this is not the leading one
SILENCE_DB = -50.0          # mean level at or below this is effectively silence
DRIFT_TOLERANCE_S = 2.0


def fail(msg):
    print('::error::%s' % msg)
    sys.exit(1)


def check_duration(path, expected):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', path],
        capture_output=True, text=True, check=True).stdout.strip()
    actual = float(out)
    drift = abs(actual - expected)
    print('1. duration %.2fs, expected %.2fs, drift %.2fs' % (actual, expected, drift))
    if drift > DRIFT_TOLERANCE_S:
        fail('Duration drift %.2fs exceeds the %.1fs tolerance. The mix is not '
             'the length the timeline predicted.' % (drift, DRIFT_TOLERANCE_S))
    return actual


def looks_like_real_id3v2(data, i):
    """True only for a structurally valid ID3v2 header at offset i.

    Searching for the raw bytes 'ID3' is not good enough. Three bytes recur by
    chance roughly once per 16MB of compressed audio, so a plain byte search
    found two 'mid-stream ID3 tags' in a 26MB file ffmpeg had just built from
    scratch - a false positive that would have failed every long episode.

    A genuine ID3v2 header is 'ID3', a major version of 2, 3 or 4, a low
    revision, then a four-byte syncsafe length whose bytes are each under 0x80.
    Requiring all of that drops the coincidence rate to effectively zero while
    still matching every real tag.
    """
    if i + 10 > len(data):
        return False
    if data[i + 3] not in (2, 3, 4):        # major version
        return False
    if data[i + 4] >= 0x10:                 # revision is always small
        return False
    size_bytes = data[i + 6:i + 10]
    if any(b >= 0x80 for b in size_bytes):  # syncsafe: high bit never set
        return False
    size = ((size_bytes[0] & 0x7f) << 21 | (size_bytes[1] & 0x7f) << 14 |
            (size_bytes[2] & 0x7f) << 7 | (size_bytes[3] & 0x7f))
    return 0 < size <= len(data) - i


def check_no_midstream_id3(path):
    data = open(path, 'rb').read()
    raw, real, i = [], [], data.find(b'ID3')
    while i != -1:
        raw.append(i)
        if looks_like_real_id3v2(data, i):
            real.append(i)
        i = data.find(b'ID3', i + 1)
    print('2. ID3 byte matches: %d at %s -> %d structurally valid at %s'
          % (len(raw), raw[:8], len(real), real[:8]))
    midstream = [h for h in real if h > FRONT_MATTER_BYTES]
    if midstream:
        fail('ID3 header found mid-stream at byte offsets %s. Strict decoders '
             'will truncate playback there.' % midstream[:5])


def mean_volume(path, start, length):
    """Mean level in dB for a window, via volumedetect.

    MEAN, not max. loudnorm plus its limiter pushes the peak of every window up
    against the true-peak ceiling, so max_volume read -2.0 / -1.9 / -1.9 dB for
    music-alone, voice, and outro alike - three windows that should differ by
    15dB looked identical, and a check written to catch a missing bed sailed
    past one. Mean level tracks actual loudness and separates them properly.

    volumedetect writes to stderr at info level, so -v error would silently
    suppress it and every window would read as 0.0. That exact mistake once
    produced a false "zero dead air" result, so the log level is pinned here.
    """
    proc = subprocess.run(
        ['ffmpeg', '-hide_banner', '-nostats', '-v', 'info',
         '-ss', str(start), '-t', str(length), '-i', path,
         '-af', 'volumedetect', '-f', 'null', '-'],
        capture_output=True, text=True)
    m = re.search(r'mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB', proc.stderr)
    if not m:
        fail('Could not read mean_volume for the window at %ss. volumedetect '
             'produced no parseable output.' % start)
    return float(m.group(1))


def check_beds_audible(path, voice_delay_ms, voice_len, outro_delay_ms, total):
    """Measure only the windows where a bed plays ALONE.

    That is what makes this decisive. Between 0 and voice_delay there is no
    voice, so if the intro bed failed to make it into the mix that window is
    digital silence rather than merely quieter. Same after the voice ends. A
    window containing both voice and music cannot distinguish a missing bed
    from a well-ducked one, so those windows are not used for the assertion.
    """
    voice_start = voice_delay_ms / 1000.0
    voice_end = voice_start + voice_len

    intro_win = (0.15, max(0.3, voice_start - 0.45))            # music alone
    body_win = (min(60.0, total / 2), 3.0)                      # voice
    outro_win = (voice_end + 0.15, max(0.3, total - voice_end - 0.25))  # music alone

    intro = mean_volume(path, *intro_win)
    body = mean_volume(path, *body_win)
    outro = mean_volume(path, *outro_win)

    print('3. mean level: intro-alone %.1fdB (%.2f-%.2fs) | body %.1fdB | '
          'outro-alone %.1fdB (%.2f-%.2fs)'
          % (intro, intro_win[0], intro_win[0] + intro_win[1], body,
             outro, outro_win[0], outro_win[0] + outro_win[1]))

    if intro <= SILENCE_DB:
        fail('No audible intro bed - the %.2fs before the voice starts is '
             'silent (%.1fdB mean). The bed is missing from the mix or was lost '
             'to a format mismatch.' % (voice_start, intro))
    if outro <= SILENCE_DB:
        fail('No audible outro bed - the tail after the voice ends is silent '
             '(%.1fdB mean).' % outro)
    if body <= SILENCE_DB:
        fail('The episode body is silent (%.1fdB mean). The voice track did not '
             'make it into the mix.' % body)


FLAT_FACTOR_LIMIT = 0.5


def check_not_clipped(path):
    """Reject a delivered file with sustained clipping.

    amix with normalize=0 sums the bed and the voice, and the result can exceed
    full scale - a real 27-minute build measured +2.0 dBTP before normalisation.
    The mixer uses a float intermediate so nothing clamps, but that has to be
    asserted rather than assumed: if anyone changes the intermediate back to
    pcm_s16le, samples clamp permanently and loudnorm cannot undo it.

    astats "Flat factor" counts consecutive identical samples, which is what
    clipping looks like. A lone peak sample reads 0.0; a clipped passage does not.
    """
    proc = subprocess.run(
        ['ffmpeg', '-hide_banner', '-nostats', '-v', 'info', '-i', path,
         '-af', 'astats=metadata=1', '-f', 'null', '-'],
        capture_output=True, text=True)
    flat = re.search(r'Flat factor:\s*(-?\d+(?:\.\d+)?)', proc.stderr)
    peak = re.search(r'Peak level dB:\s*(-?\d+(?:\.\d+)?)', proc.stderr)
    if not flat or not peak:
        fail('could not read astats for %s' % path)
    f, p = float(flat.group(1)), float(peak.group(1))
    print('4. peak %.2f dB, flat factor %.4f' % (p, f))
    if f > FLAT_FACTOR_LIMIT:
        fail('flat factor %.3f indicates sustained clipping in the delivered '
             'file. Check that the mix intermediate is float, not 16-bit.' % f)
    if p > -0.2:
        fail('peak %.2f dB is at full scale - the file is almost certainly '
             'clipped.' % p)


def main():
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    path = sys.argv[1]
    expected = float(sys.argv[2])
    voice_delay_ms = float(sys.argv[3])
    voice_len = float(sys.argv[4])
    outro_delay_ms = float(sys.argv[5])

    total = check_duration(path, expected)
    check_no_midstream_id3(path)
    check_beds_audible(path, voice_delay_ms, voice_len, outro_delay_ms, total)
    check_not_clipped(path)

    print('verification passed')


if __name__ == '__main__':
    main()
