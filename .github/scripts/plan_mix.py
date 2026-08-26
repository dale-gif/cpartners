#!/usr/bin/env python3
"""
Compute the mix timeline for CRP Audio Mix.

Kept out of the workflow YAML on purpose: this arithmetic decides where the
voice lands and where the outro comes in, and it needs to be runnable locally
against real files before anyone trusts it in CI.

Usage:
    plan_mix.py VOICE INTRO OUTRO LEAD_OUT OVERLAP

Prints key=value lines on stdout. The workflow appends them to $GITHUB_OUTPUT.
"""
import subprocess
import sys


def duration(path):
    """Length in seconds, via ffprobe."""
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', path],
        capture_output=True, text=True, check=True).stdout.strip()
    if not out:
        raise SystemExit('::error::ffprobe returned no duration for %s' % path)
    return float(out)


def main():
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)

    voice_p, intro_p, outro_p = sys.argv[1:4]
    lead_out = float(sys.argv[4])
    overlap = float(sys.argv[5])

    voice_len = duration(voice_p)
    intro_len = duration(intro_p)
    outro_len = duration(outro_p)

    # The intro bed plays alone for lead_out seconds, then fades over 1.4s while
    # the voice comes in on top. So the music is still up under the opening words
    # and gone by the time the first full sentence lands.
    fade_st = max(0.0, lead_out)
    voice_delay_ms = int(round(lead_out * 1000))

    # Warnings go to STDERR. Stdout is teed straight into $GITHUB_OUTPUT, and a
    # warning on stdout lands in that file as a malformed line - locally it was
    # also picked up by eval and run as a shell command. GitHub still surfaces
    # annotations written to stderr.
    #
    # The 50ms tolerance stops a 2ms float overrun firing this: a 4.598s bed with
    # a fade ending at 4.600s is not a real problem.
    if lead_out + 1.4 > intro_len + 0.05:
        print('::warning::intro bed is %.2fs but the fade ends at %.2fs; '
              'the tail of the fade is silence.' % (intro_len, lead_out + 1.4),
              file=sys.stderr)

    # The outro bed comes in overlap seconds before the voice finishes, so the
    # closing line rides out over music instead of hitting a hard silence.
    outro_start = max(0.0, lead_out + voice_len - overlap)
    outro_delay_ms = int(round(outro_start * 1000))

    expected = round(max(lead_out + voice_len, outro_start + outro_len), 2)

    for k, v in [
        ('voice_len', round(voice_len, 3)),
        ('intro_len', round(intro_len, 3)),
        ('outro_len', round(outro_len, 3)),
        ('fade_st', round(fade_st, 3)),
        ('voice_delay_ms', voice_delay_ms),
        ('outro_delay_ms', outro_delay_ms),
        ('expected', expected),
    ]:
        print('%s=%s' % (k, v))

    print('# voice %.2fs | intro %.2fs | outro %.2fs' % (voice_len, intro_len, outro_len),
          file=sys.stderr)
    print('# voice starts %.2fs, outro starts %.2fs, expected total %.2fs'
          % (lead_out, outro_start, expected), file=sys.stderr)


if __name__ == '__main__':
    main()
