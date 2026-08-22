import React, { useRef, useState } from 'react';
import { formatClockTime } from '../../lib/format';

const RATES = [1, 1.25, 1.5, 2];
const BAR_COUNT = 60;
// Same decorative height formula as the design reference - not real
// per-sample amplitude (that data doesn't exist), just a fixed visual
// pattern that also happens to make a clickable scrubber.
const BAR_HEIGHTS = Array.from({ length: BAR_COUNT }, (_, i) =>
  8 + Math.round(Math.abs(Math.sin(i * 0.9) * Math.cos(i * 0.31)) * 26)
);

export const AudioPlayer = ({ src }) => {
  const audioRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rateIndex, setRateIndex] = useState(0);

  if (!src) return null;

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      audio.play();
    }
  };

  const cycleRate = () => {
    const nextIndex = (rateIndex + 1) % RATES.length;
    setRateIndex(nextIndex);
    if (audioRef.current) audioRef.current.playbackRate = RATES[nextIndex];
  };

  const seekToFraction = (fraction) => {
    const audio = audioRef.current;
    if (!audio || !duration) return;
    const clamped = Math.min(1, Math.max(0, fraction));
    audio.currentTime = clamped * duration;
    setCurrentTime(audio.currentTime);
  };

  const handleSeekClick = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    seekToFraction((e.clientX - rect.left) / rect.width);
  };

  const progress = duration > 0 ? currentTime / duration : 0;
  const activeBars = Math.round(BAR_COUNT * progress);

  return (
    <div className="mt-4">
      <audio
        ref={audioRef}
        src={src}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onEnded={() => setIsPlaying(false)}
      />

      {/* Desktop player */}
      <div className="hidden lg:flex items-center gap-4 px-4 py-3.5 border border-border-strong rounded-xl bg-sidebar">
        <button
          onClick={togglePlay}
          className="flex-none w-9 h-9 rounded-full bg-linear-to-br from-brand-blue to-brand-blue-light text-white flex items-center justify-center"
        >
          {isPlaying ? '⏸' : '▶'}
        </button>
        <div
          onClick={handleSeekClick}
          className="flex-1 flex items-center gap-0.5 h-9 cursor-pointer"
        >
          {BAR_HEIGHTS.map((h, i) => (
            <span
              key={i}
              className={`flex-1 rounded-sm ${i < activeBars ? 'bg-brand-blue-light' : 'bg-border-strong'}`}
              style={{ height: `${h}px` }}
            />
          ))}
        </div>
        <span className="flex-none font-mono text-xs text-body">
          {formatClockTime(currentTime)} / {formatClockTime(duration)}
        </span>
        <button
          onClick={cycleRate}
          className="flex-none text-xs font-bold text-body border border-border rounded-md px-2 py-1"
        >
          {RATES[rateIndex]}×
        </button>
      </div>

      {/* Mobile player */}
      <div className="flex lg:hidden items-center gap-3 px-3 py-2.5 border border-border-strong rounded-xl bg-sidebar">
        <button
          onClick={togglePlay}
          className="flex-none w-8 h-8 rounded-full bg-linear-to-br from-brand-blue to-brand-blue-light text-white flex items-center justify-center text-xs"
        >
          {isPlaying ? '⏸' : '▶'}
        </button>
        <div
          onClick={handleSeekClick}
          className="flex-1 h-1 rounded-full bg-border-strong overflow-hidden cursor-pointer"
        >
          <div
            className="h-full bg-brand-blue rounded-full"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
        <span className="flex-none font-mono text-xs text-body">{formatClockTime(currentTime)}</span>
      </div>
    </div>
  );
};
