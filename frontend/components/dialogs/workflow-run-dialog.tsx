"use client"

import { useState, useRef, useEffect, useMemo } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useSession } from "next-auth/react"
import {
  streamWorkflow,
  type WorkflowStartEvent,
  type StepStartEvent,
  type StepCompleteEvent,
  type WorkflowCompleteEvent,
} from "@/lib/stream"
import { apiClient } from "@/lib/api-client"
import type { Workflow, Agent, KnowledgeBase, KBDocument } from "@/types/playground"
import {
  Play,
  Loader2,
  CheckCircle2,
  XCircle,
  Square,
  GitBranch,
  Circle,
  SkipForward,
  ChevronDown,
  ChevronRight,
  Paperclip,
  Link2,
  X,
} from "lucide-react"
import { MarkdownRenderer } from "@/components/playground/chat/markdown-renderer"
import { cn } from "@/lib/utils"
import type { FileAttachment } from "@/types/playground"

interface WorkflowRunDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  workflow: Workflow | null
  agents: Agent[]
}

interface WorkflowRunDraft {
  input?: string
  links?: string[]
}

interface WorkflowScopedInput {
  workflowId: string
  input: string
}

interface WorkflowScopedLinks {
  workflowId: string
  links: string[]
}

const WORKFLOW_DRAFT_STORAGE_PREFIX = "obsidian-ai:workflow-run-draft:"

function generateRunId(): string {
  const words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike", "nova", "oscar", "papa", "romeo", "sierra", "tango", "uniform", "victor", "whiskey", "xray", "yankee", "zulu"]
  const a = words[Math.floor(Math.random() * words.length)]
  const b = words[Math.floor(Math.random() * words.length)]
  const n = Math.floor(Math.random() * 900) + 100
  return `${a}-${b}-${n}`
}

const REPORT_PRODUCER_REPLAY_DEFAULT = `Use this prior Deep Analyst report:
[Replace with exported Markdown filename, e.g. Deep-Analyst-Research-Report.md]

Generate:
1. A polished PDF report using obsidian_export_pdf.
2. A clear PPTX presentation using export_artifact with [8/10/12] slides.

Do not repeat research. Do not call Research Agent or Deep Analyst.
The PPTX must be a synthesized presentation narrative, not copied report text.
For the PPTX, keep every slide within safe text areas: one-line title when possible, subtitle below title, no more than 4 bullets, short bullets, and a separate short takeaway/summary that cannot overlap the body text.
Return every generated filename, path, and download_url.`

function getDefaultRunInput(workflow: Workflow): string {
  const configured = workflow.config?.default_run_input
  if (typeof configured === "string" && configured.trim()) {
    return configured
  }

  if (workflow.name === "Prior Report Export") {
    return REPORT_PRODUCER_REPLAY_DEFAULT
  }

  return ""
}

function isInternalKbExportWorkflow(workflow: Workflow | null): boolean {
  if (!workflow) return false
  const choices = workflow.config?.export_action_choices
  const hasInternalExportChoices = Array.isArray(choices)
    && choices.includes("raw")
    && choices.includes("synthesis")
  return hasInternalExportChoices
    || workflow.name === "Internal KB to External KB"
}

function internalKbExportInput(params: {
  kb?: KnowledgeBase
  doc?: KBDocument
  action: string
  previous?: string
}): string {
  const previous = params.previous || ""
  const field = (name: string) => {
    const match = previous.match(new RegExp(`${name}:\\n([\\s\\S]*?)(?=\\n\\n[A-Za-z /]+:|$)`))
    return match?.[1]?.trim() || ""
  }
  const title = field("Output title")
  const filename = field("Output filename")
  const tags = field("Tags")
  const notes = field("Notes")
  return `Internal KB:
${params.kb ? `${params.kb.name} (ID: ${params.kb.id})` : "[Select an internal KB]"}

Document/File:
${params.doc ? `${params.doc.name} (ID: ${params.doc.id})` : "[Select a document/file]"}

Export action:
${params.action || "[Choose exactly one: raw | synthesis]"}

Output title:
${title || "[Optional: leave blank to use the internal document name]"}

Output filename:
${filename || "[Optional: leave blank to auto-generate a .md filename]"}

Tags:
${tags || "[Optional: comma-separated tags, e.g. ultrasound, from-obsidian-ai]"}

Notes:
${notes || "[Optional: preserve as-is by default; say if light Markdown cleanup is desired.]"}`
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ""))
    reader.onerror = () => reject(reader.error || new Error("Failed to read file"))
    reader.readAsDataURL(file)
  })
}

function mediaTypeForFile(file: File): string {
  if (file.type) return file.type
  const lower = file.name.toLowerCase()
  if (lower.endsWith(".md") || lower.endsWith(".markdown")) return "text/markdown"
  if (lower.endsWith(".txt")) return "text/plain"
  if (lower.endsWith(".pdf")) return "application/pdf"
  if (lower.endsWith(".docx")) return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  return "application/octet-stream"
}

function workflowDraftKey(workflowId: string): string {
  return `${WORKFLOW_DRAFT_STORAGE_PREFIX}${workflowId}`
}

function loadWorkflowDraft(workflowId: string): WorkflowRunDraft | null {
  try {
    const raw = window.localStorage.getItem(workflowDraftKey(workflowId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as WorkflowRunDraft
    return {
      input: typeof parsed.input === "string" ? parsed.input : undefined,
      links: Array.isArray(parsed.links) ? parsed.links.filter((link) => typeof link === "string") : [],
    }
  } catch {
    return null
  }
}

function saveWorkflowDraft(workflowId: string, draft: WorkflowRunDraft): void {
  try {
    window.localStorage.setItem(workflowDraftKey(workflowId), JSON.stringify(draft))
  } catch {
    // Draft persistence is best-effort only.
  }
}

export function WorkflowRunDialog({
  open,
  onOpenChange,
  workflow,
  agents,
}: WorkflowRunDialogProps) {
  const { data: session } = useSession()

  const [isRunning, setIsRunning] = useState(false)
  const [expandedOutputs, setExpandedOutputs] = useState<Set<number | "final">>(new Set())
  const [activeStepIndex, setActiveStepIndex] = useState<number | undefined>(undefined)
  const [activeStepIndexes, setActiveStepIndexes] = useState<Set<number>>(new Set())
  const [completedSteps, setCompletedSteps] = useState<number[]>([])
  const [stepOutputs, setStepOutputs] = useState<Record<number, string>>({})
  const [streamingStepOrder, setStreamingStepOrder] = useState<number | null>(null)
  const [finalOutput, setFinalOutput] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<"idle" | "running" | "completed" | "failed">("idle")
  const abortRef = useRef<AbortController | null>(null)
  const outputRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [currentRunLabel] = useState(generateRunId)
  const [runInput, setRunInput] = useState<WorkflowScopedInput | null>(null)
  const [attachments, setAttachments] = useState<FileAttachment[]>([])
  const [linkInput, setLinkInput] = useState("")
  const [attachedLinks, setAttachedLinks] = useState<WorkflowScopedLinks | null>(null)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const workflowId = workflow?.id ?? ""
  const isInternalKbExport = isInternalKbExportWorkflow(workflow)
  const externalReferencesEnabled = !isInternalKbExport
  const exportActionChoices = useMemo(
    () => Array.isArray(workflow?.config?.export_action_choices)
      ? workflow.config.export_action_choices.filter((choice): choice is string => typeof choice === "string")
      : [],
    [workflow],
  )
  const [internalKbs, setInternalKbs] = useState<KnowledgeBase[]>([])
  const [internalDocs, setInternalDocs] = useState<KBDocument[]>([])
  const [selectedInternalKbId, setSelectedInternalKbId] = useState("")
  const [selectedInternalDocId, setSelectedInternalDocId] = useState("")
  const [selectedExportAction, setSelectedExportAction] = useState("synthesis")
  const savedDraft = useMemo(() => {
    if (!open || !workflowId || typeof window === "undefined") return null
    return loadWorkflowDraft(workflowId)
  }, [open, workflowId])
  const currentRunInput = runInput?.workflowId === workflowId ? runInput.input : savedDraft?.input ?? null
  const currentLinks = useMemo(
    () => {
      if (!externalReferencesEnabled) return []
      return attachedLinks?.workflowId === workflowId ? attachedLinks.links : savedDraft?.links ?? []
    },
    [attachedLinks, externalReferencesEnabled, savedDraft, workflowId],
  )
  const selectedInternalKb = internalKbs.find((kb) => String(kb.id) === selectedInternalKbId)
  const selectedInternalDoc = internalDocs.find((doc) => String(doc.id) === selectedInternalDocId)

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [finalOutput, stepOutputs, streamingStepOrder])

  useEffect(() => {
    if (!open || !isInternalKbExport || !session?.accessToken) return
    apiClient.setAccessToken(session.accessToken)
    apiClient.listKnowledgeBases()
      .then(setInternalKbs)
      .catch(() => setInternalKbs([]))
  }, [open, isInternalKbExport, session?.accessToken])

  useEffect(() => {
    if (!open || !isInternalKbExport || !selectedInternalKbId || !session?.accessToken) {
      return
    }
    apiClient.setAccessToken(session.accessToken)
    apiClient.listKBDocuments(selectedInternalKbId)
      .then(setInternalDocs)
      .catch(() => setInternalDocs([]))
  }, [open, isInternalKbExport, selectedInternalKbId, session?.accessToken])

  useEffect(() => {
    if (!open || !workflowId) return
    if (currentRunInput === null && currentLinks.length === 0) return
    saveWorkflowDraft(workflowId, {
      input: currentRunInput ?? undefined,
      links: currentLinks,
    })
  }, [open, workflowId, currentRunInput, currentLinks])

  const toggleOutput = (key: number | "final") => {
    setExpandedOutputs((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const resetState = () => {
    setIsRunning(false)
    setActiveStepIndex(undefined)
    setActiveStepIndexes(new Set())
    setCompletedSteps([])
    setStepOutputs({})
    setStreamingStepOrder(null)
    setFinalOutput(null)
    setError(null)
    setStatus("idle")
    setExpandedOutputs(new Set())
    setRunInput(null)
    setAttachments([])
    setAttachedLinks(null)
    setLinkInput("")
    setAttachmentError(null)
    setInternalDocs([])
    setSelectedInternalKbId("")
    setSelectedInternalDocId("")
    setSelectedExportAction("synthesis")
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  const handleAttachFiles = async (files: FileList | null) => {
    if (!externalReferencesEnabled) return
    if (!files || files.length === 0) return
    setAttachmentError(null)
    try {
      const next = await Promise.all(
        Array.from(files).map(async (file) => ({
          filename: file.name,
          media_type: mediaTypeForFile(file),
          file_type: "document" as const,
          data: await readFileAsDataUrl(file),
        })),
      )
      setAttachments((prev) => [...prev, ...next])
    } catch (err) {
      setAttachmentError(err instanceof Error ? err.message : "Failed to attach file")
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const handleAddLink = () => {
    if (!externalReferencesEnabled) return
    const value = linkInput.trim()
    if (!value || !workflowId) return
    setAttachedLinks((prev) => ({
      workflowId,
      links: [...(prev?.workflowId === workflowId ? prev.links : currentLinks), value],
    }))
    setLinkInput("")
    setAttachmentError(null)
  }

  const workflowInputWithLinks = (input: string) => {
    if (!externalReferencesEnabled) return input
    if (currentLinks.length === 0) return input
    return `${input.trim()}\n\nBackground file links:\n${currentLinks.map((link) => `- ${link}`).join("\n")}`
  }

  const stepIndexByNodeId = (nodeId: string) => {
    const sorted = [...(workflow?.steps ?? [])].sort((a, b) => a.order - b.order)
    return sorted.findIndex((step) => step.id === nodeId)
  }

  const stepByNodeId = (nodeId: string) => {
    const sorted = [...(workflow?.steps ?? [])].sort((a, b) => a.order - b.order)
    return sorted.find((step) => step.id === nodeId)
  }

  const handleRun = async () => {
    if (!session?.accessToken || !workflow || isRunning) return

    const workflowInput = workflowInputWithLinks(currentRunInput ?? getDefaultRunInput(workflow)).trim()
    const workflowAttachments = externalReferencesEnabled ? attachments : []
    if (!workflowInput && workflowAttachments.length === 0) {
      setError("Enter workflow input before running.")
      return
    }

    setIsRunning(true)
    setStatus("running")
    setError(null)
    setCompletedSteps([])
    setActiveStepIndex(undefined)
    setStepOutputs({})
    setStreamingStepOrder(null)
    setFinalOutput(null)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      await streamWorkflow(
        session.accessToken,
        workflow.id,
        workflowInput,
        (event: WorkflowStartEvent) => { void event },
        (event: StepStartEvent) => {
          const sortedSteps = [...workflow.steps].sort((a, b) => a.order - b.order)
          const idx = sortedSteps.findIndex((s) => s.order === event.step_order)
          setActiveStepIndex(idx >= 0 ? idx : undefined)
          setActiveStepIndexes(idx >= 0 ? new Set([idx]) : new Set())
          setStreamingStepOrder(event.step_order)
        },
        () => { /* no streaming content displayed */ },
        (event: StepCompleteEvent) => {
          const sortedSteps = [...workflow.steps].sort((a, b) => a.order - b.order)
          const idx = sortedSteps.findIndex((s) => s.order === event.step_order)
          if (idx >= 0) setCompletedSteps((prev) => [...prev, idx])
          setStepOutputs((prev) => ({ ...prev, [event.step_order]: event.output }))
          setExpandedOutputs((prev) => new Set([...prev, event.step_order]))
          setActiveStepIndex(undefined)
          setActiveStepIndexes(new Set())
          setStreamingStepOrder(null)
        },
        (stepOrder: number, errorMsg: string) => {
          const sortedSteps = [...workflow.steps].sort((a, b) => a.order - b.order)
          const idx = sortedSteps.findIndex((s) => s.order === stepOrder)
          if (idx >= 0) setActiveStepIndex(idx)
          setActiveStepIndexes(idx >= 0 ? new Set([idx]) : new Set())
          setError(`Step ${stepOrder} failed: ${errorMsg}`)
          setStatus("failed")
        },
        (event: WorkflowCompleteEvent) => {
          setFinalOutput(event.final_output)
          setStatus("completed")
          setActiveStepIndex(undefined)
          setActiveStepIndexes(new Set())
          setStreamingStepOrder(null)
          setExpandedOutputs((prev) => new Set([...prev, "final" as const]))
        },
        (_rId: string, errorMsg: string) => {
          setError(errorMsg)
          setStatus("failed")
        },
        controller.signal,
        workflowAttachments,
        (event) => {
          const idx = stepIndexByNodeId(event.node_id)
          const step = stepByNodeId(event.node_id)
          setActiveStepIndex(idx >= 0 ? idx : undefined)
          if (idx >= 0) {
            setActiveStepIndexes((prev) => new Set([...prev, idx]))
          }
          setStreamingStepOrder(step?.order ?? null)
        },
        () => { /* no streaming content displayed */ },
        (event) => {
          const idx = stepIndexByNodeId(event.node_id)
          const step = stepByNodeId(event.node_id)
          if (idx >= 0) setCompletedSteps((prev) => prev.includes(idx) ? prev : [...prev, idx])
          if (step) setStepOutputs((prev) => ({ ...prev, [step.order]: event.output }))
          if (step) setExpandedOutputs((prev) => new Set([...prev, step.order]))
          if (idx >= 0) {
            setActiveStepIndexes((prev) => {
              const next = new Set(prev)
              next.delete(idx)
              return next
            })
          }
          setActiveStepIndex(undefined)
          setStreamingStepOrder(null)
        },
        (event) => {
          const idx = stepIndexByNodeId(event.node_id)
          if (idx >= 0) setActiveStepIndex(idx)
          if (idx >= 0) {
            setActiveStepIndexes((prev) => new Set([...prev, idx]))
          }
          setError(`Node ${event.node_id} failed: ${event.error}`)
          setStatus("failed")
        },
      )
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError((err as Error).message)
        setStatus("failed")
      }
    } finally {
      setIsRunning(false)
      abortRef.current = null
    }
  }

  const handleStop = () => {
    abortRef.current?.abort()
    setIsRunning(false)
    setActiveStepIndexes(new Set())
    setStatus("failed")
    setError("Cancelled by user")
  }

  const handleClose = (v: boolean) => {
    if (!isRunning) {
      resetState()
      onOpenChange(v)
    }
  }

  if (!workflow) return null

  const sortedSteps = [...workflow.steps].sort((a, b) => a.order - b.order)
  const effectiveRunInput = currentRunInput ?? getDefaultRunInput(workflow)
  const canRun = isInternalKbExport
    ? Boolean(selectedInternalKbId && selectedInternalDocId && selectedExportAction && effectiveRunInput.trim().length > 0)
    : effectiveRunInput.trim().length > 0 || attachments.length > 0 || currentLinks.length > 0
  const updateInternalKbExportInput = (params: {
    kb?: KnowledgeBase
    doc?: KBDocument | null
    action?: string
  }) => {
    if (!workflowId) return
    setRunInput({
      workflowId,
      input: internalKbExportInput({
        kb: params.kb ?? selectedInternalKb,
        doc: params.doc === null ? undefined : params.doc ?? selectedInternalDoc,
        action: params.action ?? selectedExportAction,
        previous: effectiveRunInput,
      }),
    })
  }

  const getStepName = (step: typeof sortedSteps[0]) => {
    if (step.node_type && step.node_type !== "agent") {
      return step.node_type.charAt(0).toUpperCase() + step.node_type.slice(1)
    }
    return agents.find((a) => a.id === step.agent_id)?.name || "Agent"
  }

  const nodeTypeColor: Record<string, string> = {
    start: "text-emerald-400",
    end: "text-rose-400",
    condition: "text-amber-400",
    agent: "text-indigo-400",
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="fixed! inset-4! translate-x-0! translate-y-0! top-4! left-4! max-w-none! w-[calc(100%-2rem)]! h-[calc(100vh-2rem)]! flex! flex-col! overflow-hidden p-0!">
        {/* Top bar */}
        <div className="flex items-center justify-between px-6 pr-14 pt-5 pb-4 border-b border-border shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-8 w-8 rounded-lg bg-emerald-500/10 flex items-center justify-center shrink-0">
              <GitBranch className="h-4 w-4 text-emerald-500" />
            </div>
            <div className="min-w-0">
              <DialogHeader>
                <DialogTitle className="font-mono text-base leading-tight">
                  {workflow.name.toUpperCase()}
                </DialogTitle>
              </DialogHeader>
              {workflow.description && (
                <p className="text-xs text-muted-foreground mt-0.5 truncate">{workflow.description}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-[11px] font-mono text-muted-foreground/60 hidden sm:block">
              {currentRunLabel}
            </span>
            {status === "idle" && (
              <Button onClick={handleRun} disabled={isRunning || !canRun} className="gap-2 h-8 px-4 text-xs font-mono">
                <Play className="h-3.5 w-3.5" />
                RUN
              </Button>
            )}
            {isRunning && (
              <Button variant="destructive" onClick={handleStop} className="gap-2 h-8 px-4 text-xs font-mono">
                <Square className="h-3 w-3" />
                STOP
              </Button>
            )}
            {status === "completed" && (
              <Badge className="text-[10px] bg-green-500/15 text-green-400 border-green-500/30 px-2">
                COMPLETED
              </Badge>
            )}
            {status === "failed" && (
              <Badge className="text-[10px] bg-red-500/15 text-red-400 border-red-500/30 px-2">
                FAILED
              </Badge>
            )}
            {(status === "completed" || status === "failed") && !isRunning && (
              <Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => resetState()}>
                Run Again
              </Button>
            )}
          </div>
        </div>

        {/* Main content — two column on wide, stacked on narrow */}
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* Left: pipeline steps */}
          <div className="w-64 shrink-0 border-r border-border flex flex-col overflow-y-auto py-4 px-3 gap-1">
            <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-2 mb-2">
              Pipeline · {sortedSteps.length} steps
            </p>
            {sortedSteps.map((step, i) => {
              const isActive = activeStepIndex === i || activeStepIndexes.has(i) || streamingStepOrder === step.order
              const isDone = completedSteps.includes(i)
              const output = stepOutputs[step.order]
              const isSkipped = output === "skipped"
              const isFailed = status === "failed" && activeStepIndex === i
              const nt = step.node_type || "agent"
              const color = nodeTypeColor[nt] || nodeTypeColor.agent

              return (
                <div
                  key={step.order}
                  className={cn(
                    "flex items-start gap-2.5 rounded-md px-2 py-2 transition-colors text-left",
                    isActive && "bg-green-500/10 border border-green-500/25",
                    isDone && !isActive && "bg-green-500/5 border border-green-500/15",
                    isFailed && "bg-red-500/8 border border-red-500/20",
                  )}
                >
                  <div className="shrink-0 mt-0.5">
                    {isFailed ? (
                      <XCircle className="h-3.5 w-3.5 text-red-400" />
                    ) : isActive ? (
                      <Loader2 className="h-3.5 w-3.5 text-green-400 animate-spin" />
                    ) : isDone && isSkipped ? (
                      <SkipForward className="h-3.5 w-3.5 text-muted-foreground/40" />
                    ) : isDone ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-green-400" />
                    ) : (
                      <Circle className="h-3.5 w-3.5 text-muted-foreground/30" />
                    )}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className={cn("text-[10px] font-semibold uppercase tracking-wide", color)}>
                        {nt}
                      </span>
                    </div>
                    <p className="text-xs font-medium text-foreground leading-snug truncate">
                      {getStepName(step)}
                    </p>
                    {step.task && (
                      <p className="text-[10px] text-muted-foreground leading-snug line-clamp-2 mt-0.5">
                        {step.task}
                      </p>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          {/* Right: output / idle state */}
          <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
            {status === "idle" && (
              <div className="flex-1 min-h-0 overflow-y-auto p-5">
                <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col gap-3">
                  <div className="flex items-center gap-3 text-left">
                    <div className="h-10 w-10 rounded-xl bg-emerald-500/10 flex items-center justify-center shrink-0">
                      <GitBranch className="h-5 w-5 text-emerald-500" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-foreground">Ready to run</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Enter the request for this workflow, then run it.
                      </p>
                    </div>
                  </div>
                  {isInternalKbExport && (
                    <div className="grid gap-3 rounded-md border bg-muted/20 p-3 md:grid-cols-[1fr_1fr_12rem]">
                      <div className="space-y-1.5">
                        <Label className="text-xs">Internal KB</Label>
                        <Select
                          value={selectedInternalKbId}
                          onValueChange={(value) => {
                            const nextKb = internalKbs.find((kb) => String(kb.id) === value)
                            setSelectedInternalKbId(value)
                            setSelectedInternalDocId("")
                            setInternalDocs([])
                            updateInternalKbExportInput({ kb: nextKb, doc: null })
                            if (error) setError(null)
                          }}
                        >
                          <SelectTrigger className="w-full">
                            <SelectValue placeholder="Select KB" />
                          </SelectTrigger>
                          <SelectContent>
                            {internalKbs.map((kb) => (
                              <SelectItem key={kb.id} value={String(kb.id)}>
                                {kb.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-xs">Document/File</Label>
                        <Select
                          value={selectedInternalDocId}
                          onValueChange={(value) => {
                            const nextDoc = internalDocs.find((doc) => String(doc.id) === value)
                            setSelectedInternalDocId(value)
                            updateInternalKbExportInput({ doc: nextDoc })
                            if (error) setError(null)
                          }}
                          disabled={!selectedInternalKbId}
                        >
                          <SelectTrigger className="w-full">
                            <SelectValue placeholder={selectedInternalKbId ? "Select document" : "Select KB first"} />
                          </SelectTrigger>
                          <SelectContent>
                            {internalDocs.map((doc) => (
                              <SelectItem key={doc.id} value={String(doc.id)}>
                                {doc.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-xs">Export action</Label>
                        <Select
                          value={selectedExportAction}
                          onValueChange={(value) => {
                            setSelectedExportAction(value)
                            updateInternalKbExportInput({ action: value })
                            if (error) setError(null)
                          }}
                        >
                          <SelectTrigger className="w-full">
                            <SelectValue placeholder="Action" />
                          </SelectTrigger>
                          <SelectContent>
                            {(exportActionChoices.length ? exportActionChoices : ["raw", "synthesis"]).map((choice) => (
                              <SelectItem key={choice} value={choice}>
                                {choice}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                  )}
                  <Textarea
                    value={effectiveRunInput}
                    onChange={(event) => {
                      setRunInput({ workflowId, input: event.target.value })
                      if (error) setError(null)
                    }}
                    placeholder="Ultrasound volume imaging analysis..."
                    className="w-full min-h-[min(42vh,28rem)] max-h-[46vh] resize-y overflow-y-auto text-left font-mono text-xs leading-relaxed"
                  />
                  {externalReferencesEnabled && (
                    <div className="w-full space-y-2">
                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        className="hidden"
                        onChange={(event) => void handleAttachFiles(event.target.files)}
                      />
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="gap-2 shrink-0"
                          onClick={() => fileInputRef.current?.click()}
                        >
                          <Paperclip className="h-3.5 w-3.5" />
                          Attach File
                        </Button>
                        <div className="flex min-w-0 flex-1 items-center gap-2">
                          <div className="relative flex-1">
                            <Link2 className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                            <input
                              value={linkInput}
                              onChange={(event) => setLinkInput(event.target.value)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                  event.preventDefault()
                                  handleAddLink()
                                }
                              }}
                              placeholder="https://..."
                              className="h-9 w-full rounded-md border border-input bg-transparent pl-8 pr-3 text-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                            />
                          </div>
                          <Button type="button" variant="outline" size="sm" onClick={handleAddLink} disabled={!linkInput.trim()}>
                            Add Link
                          </Button>
                        </div>
                      </div>
                      {(attachments.length > 0 || currentLinks.length > 0) && (
                        <div className="max-h-24 overflow-y-auto rounded-md border border-border/60 bg-muted/10 p-2">
                          <div className="flex flex-wrap gap-2">
                            {attachments.map((file, index) => (
                              <Badge key={`${file.filename}-${index}`} variant="outline" className="gap-1.5 px-2 py-1 text-[11px]">
                                <Paperclip className="h-3 w-3" />
                                <span className="max-w-56 truncate">{file.filename}</span>
                                <button
                                  type="button"
                                  onClick={() => setAttachments((prev) => prev.filter((_, i) => i !== index))}
                                  className="rounded-sm text-muted-foreground hover:text-foreground"
                                  aria-label={`Remove ${file.filename}`}
                                >
                                  <X className="h-3 w-3" />
                                </button>
                              </Badge>
                            ))}
                            {currentLinks.map((link, index) => (
                              <Badge key={`${link}-${index}`} variant="outline" className="gap-1.5 px-2 py-1 text-[11px]">
                                <Link2 className="h-3 w-3" />
                                <span className="max-w-72 truncate">{link}</span>
                                <button
                                  type="button"
                                  onClick={() => setAttachedLinks({
                                    workflowId,
                                    links: currentLinks.filter((_, i) => i !== index),
                                  })}
                                  className="rounded-sm text-muted-foreground hover:text-foreground"
                                  aria-label={`Remove ${link}`}
                                >
                                  <X className="h-3 w-3" />
                                </button>
                              </Badge>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  {(error || (externalReferencesEnabled && attachmentError)) && (
                    <div className="rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-400">
                      {error || attachmentError}
                    </div>
                  )}
                  <div className="sticky bottom-0 -mx-5 mt-auto border-t border-border bg-background/95 px-5 py-3 backdrop-blur">
                    <div className="mx-auto flex max-w-5xl items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2 text-[11px] text-muted-foreground/60 font-mono border border-border rounded-md px-3 py-1.5 bg-muted/20">
                        <span className="shrink-0">Run ID:</span>
                        <span className="truncate">{currentRunLabel}</span>
                      </div>
                      <Button onClick={handleRun} disabled={isRunning || !canRun} className="gap-2 shrink-0">
                        <Play className="h-4 w-4" />
                        Run Workflow
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {(status === "running" || status === "completed" || status === "failed") && (
              <div ref={outputRef} className="flex-1 overflow-y-auto p-5 space-y-1.5">
                {/* Step events — accordion */}
                {sortedSteps.map((step) => {
                  const output = stepOutputs[step.order]
                  if (!output) return null
                  const isCondition = step.node_type === "condition"
                  const isSkipped = output === "skipped"
                  const name = getStepName(step)
                  const nt = step.node_type || "agent"
                  const color = nodeTypeColor[nt] || nodeTypeColor.agent
                  // Only agent nodes with real text output can be expanded
                  const hasExpandable = !isSkipped && !isCondition && nt === "agent" && output.trim().length > 0
                  const isExpanded = expandedOutputs.has(step.order)

                  return (
                    <div key={step.order} className={cn(
                      "rounded-md border overflow-hidden text-xs transition-colors",
                      isSkipped ? "border-border/40 bg-muted/10 opacity-50" : "border-border/60 bg-muted/20",
                    )}>
                      <button
                        className={cn(
                          "w-full flex items-center gap-2.5 px-3 py-2 text-left",
                          hasExpandable && "hover:bg-muted/40 cursor-pointer",
                          !hasExpandable && "cursor-default",
                        )}
                        onClick={() => hasExpandable && toggleOutput(step.order)}
                        disabled={!hasExpandable}
                      >
                        {/* status icon */}
                        {isSkipped
                          ? <SkipForward className="h-3.5 w-3.5 text-muted-foreground/40 shrink-0" />
                          : <CheckCircle2 className="h-3.5 w-3.5 text-green-400 shrink-0" />
                        }
                        {/* name */}
                        <span className={cn("font-semibold shrink-0", color)}>{name}</span>
                        {/* inline result */}
                        {isSkipped && <span className="text-muted-foreground/50 italic">skipped</span>}
                        {!isSkipped && isCondition && (
                          <span className="text-amber-400 font-mono font-medium">→ {output}</span>
                        )}
                        {!isSkipped && !isCondition && (
                          <span className="text-muted-foreground">done</span>
                        )}
                        {/* chevron for expandable rows */}
                        {hasExpandable && (
                          <span className="ml-auto text-muted-foreground/50">
                            {isExpanded
                              ? <ChevronDown className="h-3.5 w-3.5" />
                              : <ChevronRight className="h-3.5 w-3.5" />
                            }
                          </span>
                        )}
                      </button>
                      {hasExpandable && isExpanded && (
                        <div className="px-4 pb-3 pt-1 border-t border-border/40 text-xs text-foreground max-w-none [&_pre]:my-2 [&_p]:my-1 [&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-4 [&_h1]:text-sm [&_h1]:font-bold [&_h2]:text-sm [&_h2]:font-bold [&_h3]:text-xs [&_h3]:font-semibold [&_strong]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground">
                          <MarkdownRenderer content={output} />
                        </div>
                      )}
                    </div>
                  )
                })}

                {/* Currently running */}
                {streamingStepOrder !== null && (
                  <div className="flex items-center gap-2.5 text-xs bg-green-500/8 border border-green-500/20 rounded-md px-3 py-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-green-400 shrink-0" />
                    <span className="text-green-400 font-medium">
                      {getStepName(sortedSteps.find(s => s.order === streamingStepOrder) || sortedSteps[0])}
                    </span>
                    <span className="text-muted-foreground">running...</span>
                  </div>
                )}

                {/* Final output — accordion */}
                {status === "completed" && finalOutput && (
                  <div className="mt-2 rounded-lg border border-green-500/20 bg-green-500/5 overflow-hidden">
                    <button
                      className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-green-500/8 transition-colors text-left"
                      onClick={() => toggleOutput("final")}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5 text-green-400 shrink-0" />
                      <span className="text-xs font-semibold text-green-400 flex-1">Final Output</span>
                      {expandedOutputs.has("final")
                        ? <ChevronDown className="h-3.5 w-3.5 text-green-400/60" />
                        : <ChevronRight className="h-3.5 w-3.5 text-green-400/60" />
                      }
                    </button>
                    {expandedOutputs.has("final") && (
                      <div className="px-4 py-3 border-t border-green-500/15 text-xs text-foreground max-w-none [&_pre]:my-2 [&_p]:my-1 [&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-4 [&_h1]:text-sm [&_h1]:font-bold [&_h2]:text-sm [&_h2]:font-bold [&_h3]:text-xs [&_h3]:font-semibold [&_strong]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground">
                        <MarkdownRenderer content={finalOutput} />
                      </div>
                    )}
                  </div>
                )}

                {/* Error */}
                {error && (
                  <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 flex items-start gap-2.5">
                    <XCircle className="h-3.5 w-3.5 text-red-400 shrink-0 mt-0.5" />
                    <span className="text-xs text-red-400">{error}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
