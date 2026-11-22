import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, itemApi, type MediaData } from "../api";

export function VideoThumbnail({ id, width = 96, height = 60 }: { id: string; width?: number; height?: number }) {
  const [failed, setFailed] = useState(false);
  return failed ? (
    <div className="pic none" style={{ width, height }}>Video</div>
  ) : (
    <img className="pic" src={`${itemApi(id)}/media/thumbnail`} alt="Video thumbnail"
      loading="lazy" width={width} height={height} onError={() => setFailed(true)} />
  );
}

function timestamp(seconds: number) {
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

function Frame({ url, seconds, seek, enabled }: { url: string; seconds: number; seek: () => void; enabled: boolean }) {
  const [failed, setFailed] = useState(false);
  return <div className="video-frame">
    {failed ? <p className="small">Frame unavailable</p> : <img src={url} loading="lazy" alt={`Video frame at ${timestamp(seconds)}`} onError={() => setFailed(true)} />}
    <button className="text-link" disabled={!enabled} onClick={seek}>Seek to {timestamp(seconds)}</button>
  </div>;
}

export function VideoInspection({ id }: { id: string }) {
  const player = useRef<HTMLVideoElement>(null);
  const [params] = useSearchParams();
  const [data, setData] = useState<MediaData | null>(null);
  const [error, setError] = useState("");
  const [playError, setPlayError] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [showFrames, setShowFrames] = useState(false);
  useEffect(() => {
    let live = true;
    api.media(id).then((r) => live && setData(r)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [id]);
  const seek = (seconds: number) => {
    const video = player.current;
    if (!video || video.readyState === 0 || !Number.isFinite(seconds)) return;
    video.currentTime = Math.max(0, Math.min(seconds, Number.isFinite(video.duration) ? video.duration : seconds));
  };
  const requestedTime = Number(params.get("t") ?? 0);
  useEffect(() => { seek(requestedTime); }, [requestedTime]);
  const prepare = async () => {
    setPreparing(true); setError("");
    try {
      await api.prepareVideo(id);
      setData(await api.media(id));
      setPlayError(false);
    } catch (e) { setError((e as Error).message); }
    finally { setPreparing(false); }
  };
  if (!data) return <p className={error ? "error" : "small"}>{error || "Reading video information…"}</p>;
  const playable = data.available && (data.compatible || data.prepared);
  return <div className="video-inspection">
    {playable && <video
      ref={player} className="video-player" controls playsInline preload="metadata"
      src={`${itemApi(id)}/media/video${data.prepared ? "?prepared=true" : ""}`}
      onLoadedMetadata={() => seek(requestedTime)} onError={() => setPlayError(true)}
    />}
    {playError && <p className="error">This browser could not play the recording. Prepare compatible playback or download the original.</p>}
    {data.error && <p className="error">{data.error}</p>}
    {error && <p className="error">{error}</p>}
    <div className="media-actions">
      {data.available && !data.prepared && (!data.compatible || playError) && <button className="btn" disabled={preparing} onClick={prepare}>
        {preparing ? "Preparing playback…" : "Prepare compatible playback"}
      </button>}
      {data.download && <a className="btn" href={data.download}>Download original video</a>}
    </div>
    {data.frames.length > 0 && <details className="more" onToggle={(e) => setShowFrames(e.currentTarget.open)}>
      <summary>{data.frames.every((f) => f.sampled) ? "Sampled frames" : "Scene keyframes"}</summary>
      {!playable && <p className="small">Prepare compatible playback to seek from a frame.</p>}
      {showFrames && <div className="video-frames">{data.frames.map((f) => <Frame
        key={f.seconds} seconds={f.seconds} enabled={playable}
        url={`${itemApi(id)}/media/frame?seconds=${f.seconds}`} seek={() => seek(f.seconds)}
      />)}</div>}
    </details>}
    <details className="more">
      <summary>Transcript and scenes</summary>
      {data.analysis === "not_run" && <p className="small">Video analysis has not run. No transcript or scenes have been stored.</p>}
      {data.analysis === "unavailable" && <p className="small">Video analysis is unavailable{data.analysisReason ? `: ${data.analysisReason}` : "."}</p>}
      {data.analysis === "available" && <>
        <h3>Transcript</h3>
        {data.transcript ? <pre className="transcript">{data.transcript}</pre> : <p className="small">No speech transcript was stored.</p>}
        <h3>On-screen text</h3>
        {data.onScreen.length ? <ul>{data.onScreen.map((t, n) => <li key={n}>{t}</li>)}</ul> : <p className="small">No on-screen text was stored.</p>}
        <h3>Scenes</h3>
        {data.scenes.length ? <ul>{data.scenes.map((s, n) => <li key={n}>
          <button className="text-link" disabled={!playable} onClick={() => seek(s.seconds)}>{timestamp(s.seconds)}</button> {s.text}
        </li>)}</ul> : <p className="small">No timed scenes were stored.</p>}
      </>}
    </details>
  </div>;
}
