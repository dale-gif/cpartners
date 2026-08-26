#!/usr/bin/env bash
#
# Prepend a still title card to the front of a video.
#
# The card is the CRP cover for this asset. Holding it as frame one means a platform that
# will not accept an uploaded cover file still opens on branded artwork, and a platform that
# auto-picks a thumbnail has a good chance of picking it. See the Social Media Brand Guide.
#
# Usage: prepend_title_card.sh <video> <cover-url> <hold-seconds> <output>
#
# Hold defaults to 1 second. Larry's call, 26 Aug 2026: long enough that a platform sampling an
# early frame for its auto-thumbnail is more likely to land on the card.
#
# Shared deliberately: render.yml and title-card.yml both call this. The cover engine was
# duplicated across five workflows once and every fix had to be made five times.

set -euo pipefail

VIDEO="${1:?usage: prepend_title_card.sh <video> <cover-url> <hold-seconds> <output>}"
COVER_URL="${2:?cover url required}"
HOLD="${3:-1}"
OUT="${4:?output path required}"

WORKDIR="$(mktemp -d)"
CARD="$WORKDIR/card.png"

echo "[title-card] fetching cover"
# Drive's uc?export=download can answer with an HTML interstitial rather than bytes, so follow
# redirects and then VERIFY we got an image. Handing ffmpeg an HTML page would either fail
# cryptically or, worse, produce a video we then publish.
curl -fsSL --retry 3 --retry-delay 2 -o "$CARD" "$COVER_URL"

MIME="$(file -b --mime-type "$CARD")"
case "$MIME" in
  image/*) echo "[title-card] cover ok ($MIME)" ;;
  *)
    echo "[title-card] ERROR: cover URL returned '$MIME', not an image." >&2
    echo "[title-card] Refusing to touch the video. Check the file is shared anyone-with-link." >&2
    exit 1
    ;;
esac

W="$(ffprobe -v error -select_streams v:0 -show_entries stream=width  -of csv=p=0 "$VIDEO")"
H="$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$VIDEO")"
FPS="$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$VIDEO")"
if [ -z "$FPS" ] || [ "$FPS" = "0/0" ]; then
  FPS="30"
fi

HAS_AUDIO="$(ffprobe -v error -select_streams a:0 -show_entries stream=index -of csv=p=0 "$VIDEO" 2>/dev/null || true)"

echo "[title-card] video ${W}x${H} @ ${FPS} fps, audio=${HAS_AUDIO:-none}, hold=${HOLD}s"

# The card is scaled to FIT and padded, never stretched - same rule as the cover machine.
# A 9:16 card on a 9:16 video matches exactly and pads nothing.
CARD_V="scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,fps=${FPS},format=yuv420p,setsar=1"
MAIN_V="fps=${FPS},format=yuv420p,setsar=1"

# concat demuxer needs identical encoding params; the concat FILTER re-encodes and so cannot
# desync. Slower, but this runs once per asset and correctness beats speed here.
if [ -n "${HAS_AUDIO}" ]; then
  ffmpeg -y -v warning -stats \
    -loop 1 -t "$HOLD" -i "$CARD" \
    -f lavfi -t "$HOLD" -i anullsrc=channel_layout=stereo:sample_rate=48000 \
    -i "$VIDEO" \
    -filter_complex "[0:v]${CARD_V}[card];[2:v]${MAIN_V}[main];[2:a]aresample=48000,aformat=channel_layouts=stereo[maina];[card][1:a][main][maina]concat=n=2:v=1:a=1[v][a]" \
    -map "[v]" -map "[a]" \
    -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
    -c:a aac -b:a 192k \
    -movflags +faststart "$OUT"
else
  echo "[title-card] source has no audio track - video-only concat"
  ffmpeg -y -v warning -stats \
    -loop 1 -t "$HOLD" -i "$CARD" \
    -i "$VIDEO" \
    -filter_complex "[0:v]${CARD_V}[card];[1:v]${MAIN_V}[main];[card][main]concat=n=2:v=1:a=0[v]" \
    -map "[v]" \
    -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
    -movflags +faststart "$OUT"
fi

rm -rf "$WORKDIR"
echo "[title-card] done -> $OUT"
