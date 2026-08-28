#!/usr/bin/env python3
"""
Build one finished CRP episode: theme intro, the voice, and a ducked outro bed that
swells when she stops and then plays the track out to its own ending.

Every decision below replaced something that measured wrong on a real 4:39 episode.
Read the notes before changing a number.

Usage:
    mix_episode.py --voice VOICE.mp3 --theme THEME.mp3 --out OUT.mp3 --asset-id ID
                   [--hook-at 139] [--intro-from 0] [--intro-len 16]
                   [--outro-lead-in 9.0] [--hook-delay 9.0] [--headroom 12.0]

Timeline it produces:

    0                    intro bed starts (theme --intro-from)
    intro_hold           33s of music at full, then a raised-cosine blend down
    intro_lead           the voice starts, 1.5s into that blend, music still up
    intro_hold+blend     bed has settled to the ducked level under her
    ...
    speech_end - 9.0     outro bed enters, ducked well under her voice
    speech_end           bed swells over 1.5s and the song takes the room
    +83s                 the sung "cash flow ... situation room" chorus lands
    +110s                fade begins (Larry's 1:50), constant dB per second
    +122.8s              fade complete, in the gap before the next verse

The bed level is NOT a fixed fraction - it is computed from the measured level of
both the theme and her voice, so swapping the theme retunes the duck automatically.
That has now paid off twice: first when the 47.4s cut became the 151.88s instrumental
remaster, and again on 2026-08-28 when Larry replaced that with the 197.4s Suno track
carrying the sung "Cash Flow Show / Situation Room" lyric. Each track sits at a
different level and the duck followed without anyone touching a number.
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
    # Defaults are tuned to the REMASTERED theme (151.88s, instrumental).
    # Its shape: full energy 0-56s, a breakdown around 60-68s, full again to
    # ~140s, then it winds down and resolves on its own by 151.88s.
    #
    # The earlier 47.4s cut had a SUNG hook at 14.96s and these numbers were
    # built around landing on it. The remaster has no hook, so the outro now
    # ends on the track's own resolution instead - the episode finishes when the
    # song finishes, which is better than fading over a crescendo.
    # LARRY'S SPEC, 2026-08-28, against the Suno track now in assets/audio/:
    #   "Intro: run for 33 seconds and start to fade into speech"
    #   "Outro: run at 1:50 and start to fade before the next verse at 2:03"
    #   "run the song for at least a full minute at the end"
    #   "apply better audio fading and transition from song to speech, speech to song"
    #
    # MEASURED STRUCTURE of that track (197.40s), from a word-level transcription -
    # every number below is anchored to one of these, not to taste:
    #   0.00 - 52.9   instrumental build, no vocal
    #   52.9          verse 1, "Big screens flash, green numbers rise and fall"
    #   83.2          the chorus Larry wants, "The cash flow ... in the situation room"
    #   121.10-122.78 chorus tail, "in this situation,"
    #   123.56        VERSE 2, "Big talk, big threats, big stacks on the line"
    #                 <- this is his "next verse at 2:03". He was right to 0.5s.
    #   197.40        end
    #
    # So the outro bed is the track from 0.00s - which is why hook-at equals
    # hook-delay - and it runs long enough to reach the chorus at 83.2s before
    # fading out in the 0.78s gap ahead of verse 2. That is 114s of music after
    # her last word, comfortably past his "at least a full minute".
    #
    # THE INTRO STARTS AT 79.6s, NOT 0. Larry asked for two things at once:
    #   "use the correct song that features the cash flow show lyrics EARLY"
    #   "intro: run for 33 seconds and start to fade into speech"
    # An intro taken from 0.00 satisfies the second and fails the first - the track
    # is instrumental until 52.9s, so 33 seconds from the top has no vocal in it.
    # Starting at 79.64s satisfies both: that is the downbeat of the pre-chorus line
    # "cuz it all goes down in the situation", and the sung "The cash flow show"
    # lands at 82.40s - 2.8 SECONDS INTO THE EPISODE - with the hook then repeating
    # four times inside the 33 seconds. Rounded to 79.6 to enter just before the
    # vocal rather than clipping its first consonant.
    #
    # 79.64 is a vocal downbeat, not a silence, so the bed eases in over
    # --intro-fade-in rather than cutting. A cut here clicks; 0.5s was audible as a
    # bump on the very first sound of the show, which is the worst place for one.
    ap.add_argument('--hook-at', type=float, default=9.0,
                    help='musical moment the swell lands on; minus hook-delay gives the bed trim point')
    ap.add_argument('--intro-from', type=float, default=79.6,
                    help='theme offset the intro enters on - the pre-chorus lead-in')
    ap.add_argument('--intro-fade-in', type=float, default=0.8,
                    help='ease-in on the first sound of the episode, since 79.6s is mid-track')
    ap.add_argument('--intro-hold', type=float, default=33.0,
                    help="seconds of music at full before the blend starts - Larry's 33")
    ap.add_argument('--intro-blend', type=float, default=6.0,
                    help='length of the ramp from full down to the ducked bed')
    # 79.6 + 43.5 = 123.1s, which stops the intro bed short of verse 2 at 123.56s.
    # The old 47.0 would have run it to 126.6 and pulled the first line of that verse
    # in under her opening words. The --verse-at guard below enforces this.
    ap.add_argument('--intro-len', type=float, default=43.5)
    ap.add_argument('--intro-lead', type=float, default=34.5,
                    help='seconds of intro before the voice starts')
    ap.add_argument('--outro-lead-in', type=float, default=9.0)
    ap.add_argument('--hook-delay', type=float, default=9.0,
                    help='seconds from bed start to that moment landing')
    ap.add_argument('--outro-len', type=float, default=123.0,
                    help='bed is silent well before this, but it never reaches verse 2')
    ap.add_argument('--headroom', type=float, default=12.0)
    ap.add_argument('--fade-start', type=float, default=110.0,
                    help="Larry's 1:50 - where the outro fade begins")
    ap.add_argument('--fade-len', type=float, default=12.8,
                    help='completes at 122.8s, in the gap before the 123.56s verse')
    # The fade must be FINISHED before the next verse begins, otherwise the episode
    # ends on a half-faded new vocal - the exact thing Larry asked to avoid. This is
    # the measured onset of "Big talk, big threats", not an estimate, and it is
    # asserted below instead of being left as a comment somebody can drift away from.
    ap.add_argument('--verse-at', type=float, default=123.56,
                    help='onset of the verse the outro fade must complete before; '
                         '0 disables the check for a theme with no such verse')
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

    # Larry's rule, asserted rather than assumed. NEITHER bed may straddle the verse.
    # Positions are in THEME time, so the bed's own start has to be added back - the
    # fade expression inside the filter graph is relative to bed start, not to it.
    #
    # Both beds are checked, not just the outro. The intro now enters at 79.6s, only
    # 44s short of the verse, so it is just as capable of dragging that vocal in - and
    # under her opening words, where it would be even more obvious.
    if a.verse_at > 0:
        for lbl, start, length in [('intro', a.intro_from, a.intro_len),
                                   ('outro', om_in, a.outro_len)]:
            if start >= a.verse_at:
                continue          # a bed that begins after the verse cannot straddle it
            if start + length > a.verse_at:
                fail('the %s bed runs theme %.2f-%.2fs, straddling the verse at '
                     '%.2fs. It would pull that vocal into the episode. Shorten '
                     '--%s-len.' % (lbl, start, start + length, a.verse_at, lbl))
        fade_ends = om_in + a.fade_start + a.fade_len
        if fade_ends > a.verse_at:
            fail('the outro fade finishes at theme %.2fs but the next verse starts '
                 'at %.2fs. The episode would end on a half-faded new vocal. Move '
                 '--fade-start or shorten --fade-len.' % (fade_ends, a.verse_at))
        print('  intro bed theme %.2f-%.2fs | outro fade completes at theme %.2fs, '
              '%.2fs clear of the %.2fs verse'
              % (a.intro_from, a.intro_from + a.intro_len, fade_ends,
                 a.verse_at - fade_ends, a.verse_at))

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
    # Full for intro_hold, then a raised-cosine ramp down to the ducked bed over
    # intro_blend. Derived from Larry's numbers instead of from intro_len, so the
    # hold is exactly what he asked for and the blend can be widened independently.
    in_g = '1+(%s-1)*%s' % (duck_in, scurve('t', a.intro_hold, a.intro_hold + a.intro_blend))
    fi = scurve('t', 0.0, 0.6)
    sw = '(%s+(1-%s)*%s)' % (duck_out, duck_out, scurve('t', a.outro_lead_in,
                                                        a.outro_lead_in + 1.5))
    fo = db_fade(a.fade_start, a.fade_len)
    out_g = '%s*%s*%s' % (fi, sw, fo)

    fg = (
        "[0:a]atrim=%s:%s,asetpts=N/SR/TB,%s,afade=t=in:st=0:d=%s,"
        "volume='%s':eval=frame,afade=t=out:st=%s:d=%s[im];"
        "[1:a]%s,adelay=%d:all=1[ep];"
        "[2:a]atrim=%s:%s,asetpts=N/SR/TB,%s,volume='%s':eval=frame,adelay=%d:all=1[om];"
        "[im][ep][om]amix=inputs=3:duration=longest:normalize=0[a]"
        % (a.intro_from, a.intro_from + a.intro_len, A, a.intro_fade_in, in_g,
           a.intro_hold + a.intro_blend, max(1.0, a.intro_len - a.intro_hold - a.intro_blend),
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
