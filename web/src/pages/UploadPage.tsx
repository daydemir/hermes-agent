import { useEffect, useMemo, useState } from "react";
import { Upload, CheckCircle2, Copy, FileAudio, Loader2, UserRound } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { api, type AudioAnalysisResponse, type AudioUploadResponse } from "@/lib/api";

const ACCEPTED_AUDIO = [
  ".aac",
  ".aif",
  ".aiff",
  ".flac",
  ".m4a",
  ".mp3",
  ".mp4",
  ".oga",
  ".ogg",
  ".opus",
  ".wav",
  ".webm",
].join(",");

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function speakerQuestion(analysis: AudioAnalysisResponse | null): string {
  const speakers = analysis?.speakers ?? [];
  if (speakers.length < 1) return "";
  const lines = speakers.map((speaker) => `${speaker.speaker}: “${speaker.example}”`);
  return `Who are these speakers in the uploaded phone call?\n\n${lines.join("\n\n")}`;
}

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<AudioUploadResponse | null>(null);
  const [analysis, setAnalysis] = useState<AudioAnalysisResponse | null>(null);
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({});
  const [savingNames, setSavingNames] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const helper = useMemo(() => {
    if (!file) return "Pick an audio recording from your phone.";
    return `${file.name} · ${formatBytes(file.size)}`;
  }, [file]);

  useEffect(() => {
    if (!result?.stored_name) return;
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const next = await api.getAudioAnalysis(result!.stored_name);
        if (cancelled) return;
        setAnalysis(next);
        if (next.speaker_names) setSpeakerNames(next.speaker_names);
        if (["queued", "running", "not_started"].includes(next.status)) {
          timer = window.setTimeout(poll, 2500);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [result?.stored_name]);

  async function onUpload() {
    if (!file || uploading) return;
    setUploading(true);
    setError(null);
    setResult(null);
    setAnalysis(null);
    setSpeakerNames({});
    setCopied(false);
    try {
      setResult(await api.uploadAudio(file));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  async function copyText(text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function saveSpeakerNames() {
    if (!result) return;
    setSavingNames(true);
    setError(null);
    try {
      setAnalysis(await api.setAudioSpeakers(result.stored_name, speakerNames));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingNames(false);
    }
  }

  const question = speakerQuestion(analysis);
  const analysisBusy = analysis && ["queued", "running", "not_started"].includes(analysis.status);

  return (
    <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6">
      <div>
        <Typography className="font-mondwest text-display text-2xl uppercase tracking-[0.12em]">
          Upload audio
        </Typography>
        <p className="mt-2 text-sm text-text-secondary">
          Upload a phone call recording. Rolly will transcribe it, diarize speakers, then ask who each speaker is.
        </p>
      </div>

      <label className="flex min-h-52 cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-current/25 bg-background-muted/40 p-6 text-center transition hover:border-current/50">
        <FileAudio className="h-10 w-10 text-text-secondary" />
        <div className="space-y-1">
          <div className="text-base font-medium">Choose audio file</div>
          <div className="text-sm text-text-secondary">{helper}</div>
        </div>
        <input
          className="sr-only"
          type="file"
          accept={`${ACCEPTED_AUDIO},audio/*,video/mp4,video/webm`}
          onChange={(event) => {
            setResult(null);
            setAnalysis(null);
            setError(null);
            setSpeakerNames({});
            setFile(event.target.files?.[0] ?? null);
          }}
        />
      </label>

      <Button
        className="h-12 w-full justify-center text-base sm:w-fit sm:px-8"
        disabled={!file || uploading}
        onClick={onUpload}
      >
        {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
        {uploading ? "Uploading…" : "Upload + diarize"}
      </Button>

      {error ? (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
          {error}
        </div>
      ) : null}

      {result ? (
        <div className="rounded-xl border border-current/15 bg-background-muted/40 p-4 text-sm">
          <div className="mb-3 flex items-center gap-2 text-green-300">
            <CheckCircle2 className="h-4 w-4" />
            Uploaded {formatBytes(result.size_bytes)}
          </div>
          <div className="break-all rounded bg-black/20 p-3 font-mono text-xs text-text-secondary">
            {result.path}
          </div>
        </div>
      ) : null}

      {analysisBusy ? (
        <div className="flex items-center gap-2 rounded-xl border border-current/15 bg-background-muted/40 p-4 text-sm text-text-secondary">
          <Loader2 className="h-4 w-4 animate-spin" />
          Diarizing/transcribing… longer calls can take a bit.
        </div>
      ) : null}

      {analysis?.status === "failed" ? (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
          Diarization failed: {analysis.error}
        </div>
      ) : null}

      {question ? (
        <div className="rounded-xl border border-current/15 bg-background-muted/40 p-4 text-sm">
          <div className="mb-3 flex items-center gap-2 font-medium">
            <UserRound className="h-4 w-4" />
            Who said what?
          </div>
          <div className="space-y-3">
            {(analysis?.speakers ?? []).map((speaker) => (
              <label key={speaker.speaker} className="block space-y-1">
                <div className="text-xs uppercase tracking-wide text-text-tertiary">{speaker.speaker}</div>
                <div className="rounded bg-black/20 p-3 text-xs text-text-secondary">“{speaker.example}”</div>
                <input
                  className="w-full rounded border border-current/15 bg-background px-3 py-2 text-sm outline-none focus:border-current/40"
                  placeholder={`Name for ${speaker.speaker}`}
                  value={speakerNames[speaker.speaker] ?? ""}
                  onChange={(event) => setSpeakerNames((prev) => ({ ...prev, [speaker.speaker]: event.target.value }))}
                />
              </label>
            ))}
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Button className="justify-center" disabled={savingNames} onClick={saveSpeakerNames}>
              {savingNames ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Save speaker names
            </Button>
            <Button className="justify-center" onClick={() => copyText(question)}>
              <Copy className="mr-2 h-4 w-4" />
              {copied ? "Copied" : "Copy question for chat"}
            </Button>
          </div>
        </div>
      ) : null}

      {analysis?.named_transcript ? (
        <div className="rounded-xl border border-current/15 bg-background-muted/40 p-4 text-sm">
          <div className="mb-2 font-medium">Transcript</div>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-3 text-xs text-text-secondary">
            {analysis.named_transcript}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
