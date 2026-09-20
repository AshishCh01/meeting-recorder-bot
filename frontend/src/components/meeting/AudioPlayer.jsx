import React, { useRef, useState } from 'react';
import { Play, Pause } from 'lucide-react';
import { formatClockTime } from '../../lib/format';

const RATES = [1, 1.25, 1.5, 2];

/**
 * A slim bar pinned to the bottom of the meeting column.
 *
 * It used to sit in the header as a 60-bar waveform, which looked like data it
 * was not - the heights came from a sine formula, not the audio - and cost a
 * third of the header on a screen where the summary was already fighting for
 * room. One bar, always reachable, no fake waveform.
 *
 * The scrubber is a 4px track inside a 22px row. The track is what you see;
 * the row is what you hit. The previous mobile scrubber was a bare 4px line,
 * which on a touch screen is close to untappable.
 */
// `gutterClass` stops the bar bleeding into the Ask AI column when it is
// open; see the note beside it in MeetingDetails.
export const AudioPlayer = ({ src, gutterClass = '' }) => {
  const audioRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rateIndex, setRateIndex] = useState(0);

  if (!src) return null;

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) audio.pause();
    else audio.play();
  };

  const cycleRate = () => {
    const next = (rateIndex + 1) % RATES.length;
    setRateIndex(next);
    if (audioRef.current) audioRef.current.playbackRate = RATES[next];
  };

  const seekToFraction = (fraction) => {
    const audio = audioRef.current;
    if (!audio || !duration) return;
    audio.currentTime = Math.min(1, Math.max(0, fraction)) * duration;
    setCurrentTime(audio.currentTime);
  };

  const handleSeekClick = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    seekToFraction((e.clientX - rect.left) / rect.width);
  };

  const handleSeekKey = (e) => {
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      seekToFraction((currentTime + 5) / duration);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      seekToFraction((currentTime - 5) / duration);
    }
  };

  const percent = duration > 0 ? Math.round((currentTime / duration) * 100) : 0;

  return (
    <div
      className={`sticky bottom-(--mobile-nav-h) z-15 -mx-4 mt-auto flex items-center gap-3 border-t border-line bg-surface/85 px-4 py-2.5 backdrop-blur-lg tablet:bottom-0 tablet:-mx-6 tablet:px-6 lg:-mx-8 lg:px-8 ${gutterClass}`}
    >
      <audio
        ref={audioRef}
        src={src}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onEnded={() => setIsPlaying(false)}
      />

      <button
        type="button"
        onClick={togglePlay}
        aria-pressed={isPlaying}
        aria-label={isPlaying ? 'Pause' : 'Play'}
        className="btn-primary grid h-9 w-9 flex-none place-items-center rounded-full"
      >
        {isPlaying ? <Pause className="h-3.5 w-3.5 fill-current" /> : <Play className="h-3.5 w-3.5 fill-current" />}
      </button>

      <div
        role="slider"
        tabIndex={0}
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        onClick={handleSeekClick}
        onKeyDown={handleSeekKey}
        className="flex h-[22px] min-w-0 flex-1 cursor-pointer items-center"
      >
        <div className="h-1 w-full overflow-hidden rounded-[3px] bg-tint-3">
          <div
            className="h-full rounded-[3px] bg-linear-to-r from-brand-blue to-brand-blue-light"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      <time className="flex-none font-mono text-[11.5px] text-muted">
        {formatClockTime(currentTime)} / {formatClockTime(duration)}
      </time>

      <button
        type="button"
        onClick={cycleRate}
        aria-label={`Playback speed ${RATES[rateIndex]} times`}
        className="flex-none rounded-md border border-line px-2 py-1 text-xs font-bold text-body transition-colors hover:border-border hover:text-brand-dark"
      >
        {RATES[rateIndex]}×
      </button>
    </div>
  );
};
