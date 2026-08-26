#!/usr/bin/env python3
"""
Build one finished CRP episode: sung-hook intro, the voice, and a ducked outro bed
that swells onto the hook after the last word.

Everything here was derived from a real 4:39 episode and a 47.4s theme, and each
decision below replaced something that measured wrong. Read the notes before
changing a number.

Usage:
    mix_episode.py --voice VOICE.mp3 --theme THEME.mp3 --out OUT.mp3 --asset-id ID
                   [--hook-at 14.96] [--intro-from 28.5] [--intro-len 16]
                   [--outro-lead-in 9.0] [--hook-delay 10.0] [--headroom 12.0]

Timeline it produces:

    0                    intro bed starts (theme --intro-from)
    intro_len-2.6        bed eases down under the episode's own opening
    intro_lead           the voice starts
    ...
    speech_end - 9.0     outro bed enters, ducked well under her voice
    speech_end           bed swells over 1.5s
    speech_end + 1.0     the sung hook lands
    ...                  dB-linear fade to silence
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys

TARGET_LUFS = -16.0
TARGET_TP = -1.5
TARGET_LRA = 11
SR = 44100


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fail(msg):
    print('::error::%s' % msg)
    sys.exit(1)


def duration(path):
    out = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'csv=p=0', path]).stdout.strip()
    if not out:
        fail('ffprobe returned no duration for %s' % path)
    return float(out)


def mean_dbfs(path, start, length):
    """Mean level of a window, in dBFS.

    volumedetect writes to stderr at INFO level. Passing -v error silently
    suppresses it and every reading comes back 0.0 - that exact mistake once
    produced a false "zero dead air" result, so the log level is pinned.
    """
    p = run(['ffmpeg', '-hide_banner', '-nostats', '-v', 'info',
             '-ss', str(start), '-t', str(length), '-i', path,
             '-af', 'volumedetect', '-f', 'null', '-'])
    m = re.search(r'mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB', p.stderr)
    if not m:
        fail('could not read mean_volume for %s at %ss' % (path, start))
    return float(m.group(1))


SILENCE_DB = -50.0


def speech_end(path, total):
    """Where the last word actually finishes.

    TTS output ends close to the last word but not exactly - a measured episode
    had 0.28s of tail. The outro bed lands N seconds before she STOPS, not before
    the file stops, so trailing silence has to come off or the whole outro sits
    late and the hook misses its mark.

    Do NOT infer the trailing silence by pairing silence_start with silence_end.
    ffmpeg emits a silence_end at EOF, so "last start is after last end" is false
    and the trailing silence is missed entirely - that bug put the bed 0.42s late.
    Instead take the last silence_start and CONFIRM the region after it is quiet.

    silencedetect writes to stderr at info level; -v error would suppress it and
    this would silently always return `total`.
    """
    p = run(['ffmpeg', '-hide_banner', '-nostats', '-v', 'info', '-i', path,
             '-af', 'silencedetect=noise=%ddB:d=0.15' % SILENCE_DB, '-f', 'null', '-'])
    starts = [float(x) for x in re.findall(r'silence_start:\s*(-?[\d.]+)', p.stderr)]
    if not starts:
        print('  no trailing silence detected; using full duration')
        return total
    cand = starts[-1]
    if cand <= 0 or cand >= total:
        return total
    tail = mean_dbfs(path, cand, total - cand)
    if tail <= SILENCE_DB:
        print('  trailing silence from %.3fs (tail mean %.1f dB, %.3fs trimmed)'
              % (cand, tail, total - cand))
        return cand
    print('  candidate silence at %.3fs rejected (tail mean %.1f dB is not quiet)'
          % (cand, tail))
    return total


def scurve(var, t0, t1):
    """Raised-cosine ramp 0..1 as an ffmpeg expression.

    Zero slope at both ends, so there is no audible corner. A hard step lurches
    and a linear ramp still clicks perceptually; this does neither.
    """
    return '(0.5-0.5*cos(PI*clip((%s-%s)/%s,0,1)))' % (var, t0, t1 - t0)


def db_fade(t0, seconds, depth_db=50.0):
    """A fade that is LINEAR IN dB - constant dB per second.

    ffmpeg's afade default curve (tri) is linear in AMPLITUDE, which loses only
    about 6 dB across its first half. On a bed whose music is building underneath,
    that reads as no fade at all followed by a cliff - the exact defect Dale heard.
    """
    return 'pow(10,-%s*clip((t-%s)/%s,0,1))' % (depth_db / 20.0, t0, seconds)


def derive_duck(theme_db, voice_db, headroom_db):
    """Gain factor that puts the bed `headroom_db` under her speech.

    NOT a fixed fraction. The theme is a mastered track averaging about -15.7 dB
    while her voice averages about -28.9 dB. A "duck" of 0.28 (-11 dB) leaves the
    music sitting ON TOP of the voice - measured 4.5 dB ABOVE it in one window.
    The duck has to be computed from the two actual levels.
    """
    want_db = voice_db - headroom_db
    gain_db = want_db - theme_db
    return 10 ** (gain_db / 20.0), gain_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--voice', required=True)
    ap.add_argument('--theme', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--asset-id', required=True)
    ap.add_argument('--hook-at', type=float, default=14.96,
                    help='where the sung hook starts in the theme')
    ap.add_argument('--intro-from', type=float, default=28.5)
    ap.add_argument('--intro-len', type=float, default=16.0)
    ap.add_argument('--intro-lead', type=float, default=13.4,
                    help='seconds of intro before the voice starts')
    ap.add_argument('--outro-lead-in', type=float, default=9.0)
    ap.add_argument('--hook-delay', type=float, default=10.0,
                    help='seconds from bed start to the hook landing')
    ap.add_argument('--outro-len', type=float, default=18.5)
    ap.add_argument('--headroom', type=float, default=12.0)
    ap.add_argument('--fade-start', type=float, default=14.5)
    ap.add_argument('--fade-len', type=float, default=4.0)
    a = ap.parse_args()

    v_len = duration(a.voice)
    t_len = duration(a.theme)
    sp_end = speech_end(a.voice, v_len)

    theme_db = mean_dbfs(a.theme, a.hook_at - a.hook_delay, a.outro_len)
    vox_end_db = mean_dbfs(a.voice, max(0.0, sp_end - a.outro_lead_in), a.outro_lead_in)
    vox_open_db = mean_dbfs(a.voice, 0.5, min(12.0, v_len - 1))

    duck_out, duck_out_db = derive_duck(theme_db, vox_end_db, a.headroom)
    duck_in, duck_in_db = derive_duck(theme_db, vox_open_db, a.headroom)

    om_in = a.hook_at - a.hook_delay
    if om_in < 0:
        fail('hook-delay %.1fs exceeds hook-at %.1fs' % (a.hook_delay, a.hook_at))
    for lbl, start, length in [('intro', a.intro_from, a.intro_len),
                               ('outro', om_in, a.outro_len)]:
        if start + length > t_len + 0.01:
            fail('%s bed needs theme %.2f-%.2fs but the theme is %.2fs. '
                 'The fade would be truncated and the audio would cut off.'
                 % (lbl, start, start + length, t_len))

    speech_end_mix = a.intro_lead + sp_end
    om_start = speech_end_mix - a.outro_lead_in
    total = round(om_start + a.outro_len, 3)

    print('  voice %.3fs, last word at %.2fs (%.2fs tail)' % (v_len, sp_end, v_len - sp_end))
    print('  theme %.2fs at %.1f dB | her close %.1f dB | her open %.1f dB'
          % (t_len, theme_db, vox_end_db, vox_open_db))
    print('  duck: intro %.3f (%.1f dB), outro %.3f (%.1f dB) - %.0f dB under her'
          % (duck_in, duck_in_db, duck_out, duck_out_db, a.headroom))
    print('  outro bed enters %.2fs (theme %.2f), swells at %.2fs, hook at %.2fs'
          % (om_start, om_in, speech_end_mix, om_start + a.hook_delay))
    print('  total %.2fs' % total)

    A = 'aformat=sample_rates=%d:channel_layouts=mono' % SR
    in_g = '1+(%s-1)*%s' % (duck_in, scurve('t', a.intro_len - 4.4, a.intro_len - 1.8))
    fi = scurve('t', 0.0, 0.6)
    sw = '(%s+(1-%s)*%s)' % (duck_out, duck_out, scurve('t', a.outro_lead_in,
                                                        a.outro_lead_in + 1.5))
    fo = db_fade(a.fade_start, a.fade_len)
    out_g = '%s*%s*%s' % (fi, sw, fo)

    fg = (
        "[0:a]atrim=%s:%s,asetpts=N/SR/TB,%s,afade=t=in:st=0:d=0.5,"
        "volume='%s':eval=frame,afade=t=out:st=%s:d=3[im];"
        "[1:a]%s,adelay=%d:all=1[ep];"
        "[2:a]atrim=%s:%s,asetpts=N/SR/TB,%s,volume='%s':eval=frame,adelay=%d:all=1[om];"
        "[im][ep][om]amix=inputs=3:duration=longest:normalize=0[a]"
        % (a.intro_from, a.intro_from + a.intro_len, A, in_g, a.intro_len - 3,
           A, int(round(a.intro_lead * 1000)),
           om_in, om_in + a.outro_len, A, out_g, int(round(om_start * 1000)))
    )

    # Pass 1 to a FLOAT intermediate. amix with normalize=0 sums the bed and the
    # voice and the result can exceed full scale - a real build measured +2.0 dBTP.
    # 16-bit would clamp that permanently, baking in distortion loudnorm cannot undo.
    raw = a.out + '.raw.wav'
    p = run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'error',
             '-i', a.theme, '-i', a.voice, '-i', a.theme,
             '-filter_complex', fg, '-map', '[a]', '-vn',
             '-ar', str(SR), '-ac', '1', '-c:a', 'pcm_f32le', raw])
    if p.returncode != 0:
        fail('mix pass 1 failed: %s' % p.stderr[-800:])

    # Two-pass loudness. A single pass estimates as it goes and drifts 1-2 LU on a
    # long file; measured on a real excerpt, single-pass landed -1.11 LU off target
    # and two-pass -0.64.
    p = run(['ffmpeg', '-hide_banner', '-nostats', '-i', raw, '-af',
             'loudnorm=I=%s:TP=%s:LRA=%s:print_format=json' % (TARGET_LUFS, TARGET_TP, TARGET_LRA),
             '-f', 'null', '-'])
    mj = re.search(r'\{.*?\}', p.stderr, re.S)
    if not mj:
        fail('could not parse the loudnorm measurement. Refusing to guess.')
    meas = json.loads(mj.group(0))
    need = ['input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset']
    if any(k not in meas for k in need):
        fail('loudnorm measurement missing keys: %s' % [k for k in need if k not in meas])
    print('  measured %s LUFS / TP %s' % (meas['input_i'], meas['input_tp']))

    # linear=true applies ONE constant gain. Dynamic normalisation would ride the
    # level and partly undo the ducking built in pass 1.
    p = run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-i', raw,
             '-af', 'loudnorm=I=%s:TP=%s:LRA=%s:measured_I=%s:measured_TP=%s:'
                    'measured_LRA=%s:measured_thresh=%s:offset=%s:linear=true'
                    % (TARGET_LUFS, TARGET_TP, TARGET_LRA, meas['input_i'],
                       meas['input_tp'], meas['input_lra'], meas['input_thresh'],
                       meas['target_offset']),
             '-ar', str(SR), '-ac', '1', '-b:a', '128k', '-codec:a', 'libmp3lame',
             '-metadata', 'title=%s' % a.asset_id,
             '-metadata', 'artist=Corporate Recovery Partners',
             '-metadata', 'album=The Cash Flow Show',
             '-metadata', 'genre=Business', a.out])
    if p.returncode != 0:
        fail('encode pass 2 failed: %s' % p.stderr[-800:])
    os.remove(raw)

    with open('mix_plan.json', 'w') as f:
        json.dump({'total': total, 'voice_delay_ms': int(round(a.intro_lead * 1000)),
                   'voice_len': v_len, 'speech_end_mix': speech_end_mix,
                   'outro_start': om_start, 'hook_at_mix': om_start + a.hook_delay,
                   'duck_in': duck_in, 'duck_out': duck_out}, f, indent=1)
    print('  wrote %s and mix_plan.json' % a.out)


if __name__ == '__main__':
    main()
