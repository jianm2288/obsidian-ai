"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useSession } from "next-auth/react"
import { useParams, useRouter } from "next/navigation"
import { apiClient } from "@/lib/api-client"
import type { KnowledgeBase, KBDocument } from "@/types/playground"
import { usePermissionsStore } from "@/stores/permissions-store"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  ArrowLeft,
  Trash2,
  FileText,
  Type,
  Upload,
  CheckCircle2,
  Clock,
  Loader2,
  Globe,
  Copy,
} from "lucide-react"
import { toast } from "sonner"
import { useConfirm } from "@/hooks/use-confirm"
import { MarkdownRenderer } from "@/components/playground/chat/markdown-renderer"

type AddMode = "text" | "file" | null

function errorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

export default function KnowledgeDetailPage() {
  const { data: authSession } = useSession()
  const params = useParams()
  const router = useRouter()
  const kbId = params.id as string

  const canManageKB = usePermissionsStore((s) => s.permissions.create_knowledge_bases)

  const [kb, setKb] = useState<KnowledgeBase | null>(null)
  const [documents, setDocuments] = useState<KBDocument[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [addMode, setAddMode] = useState<AddMode>(null)
  const [viewingDoc, setViewingDoc] = useState<KBDocument | null>(null)
  const [isLoadingViewingDoc, setIsLoadingViewingDoc] = useState(false)

  // Text form state
  const [textName, setTextName] = useState("")
  const [textContent, setTextContent] = useState("")
  const [textLoading, setTextLoading] = useState(false)

  // File form state
  const [fileName, setFileName] = useState("")
  const [fileData, setFileData] = useState<string | null>(null)
  const [fileFilename, setFileFilename] = useState<string | null>(null)
  const [fileMediaType, setFileMediaType] = useState<string | null>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [ConfirmDialog, confirmDelete] = useConfirm({
    title: "Delete document",
    description: "This will permanently delete this document from the knowledge base.",
    confirmLabel: "Delete",
    variant: "destructive",
  })

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const [kbData, docs] = await Promise.all([
        apiClient.getKnowledgeBase(kbId),
        apiClient.listKBDocuments(kbId),
      ])
      setKb(kbData)
      setDocuments(docs)
    } catch {
      toast.error("Failed to load knowledge base")
    } finally {
      setIsLoading(false)
    }
  }, [kbId])

  useEffect(() => {
    if (!authSession?.accessToken) return
    apiClient.setAccessToken(authSession.accessToken as string)
    void Promise.resolve().then(load)
  }, [authSession?.accessToken, load])

  const handleAddText = async () => {
    if (!textName.trim() || !textContent.trim()) return
    setTextLoading(true)
    try {
      const doc = await apiClient.addKBDocument(kbId, {
        doc_type: "text",
        name: textName.trim(),
        content_text: textContent.trim(),
      })
      setDocuments((prev) => [...prev, doc])
      setKb((prev) => prev ? { ...prev, document_count: prev.document_count + 1 } : prev)
      setAddMode(null)
      setTextName("")
      setTextContent("")
      toast.success("Document added and indexed")
    } catch (err: unknown) {
      toast.error(errorMessage(err, "Failed to add document"))
    } finally {
      setTextLoading(false)
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setFileFilename(file.name)
    setFileMediaType(file.type)
    if (!fileName) setFileName(file.name.replace(/\.[^.]+$/, ""))
    const reader = new FileReader()
    reader.onload = (ev) => {
      setFileData(ev.target?.result as string)
    }
    reader.readAsDataURL(file)
  }

  const handleAddFile = async () => {
    if (!fileName.trim() || !fileData || !fileFilename) return
    setFileLoading(true)
    try {
      const doc = await apiClient.addKBDocument(kbId, {
        doc_type: "file",
        name: fileName.trim(),
        file_data: fileData,
        filename: fileFilename,
        media_type: fileMediaType || "application/octet-stream",
      })
      setDocuments((prev) => [...prev, doc])
      setKb((prev) => prev ? { ...prev, document_count: prev.document_count + 1 } : prev)
      setAddMode(null)
      setFileName("")
      setFileData(null)
      setFileFilename(null)
      setFileMediaType(null)
      if (fileInputRef.current) fileInputRef.current.value = ""
      toast.success("File uploaded and indexed")
    } catch (err: unknown) {
      toast.error(errorMessage(err, "Failed to upload file"))
    } finally {
      setFileLoading(false)
    }
  }

  const handleDeleteDoc = async (doc: KBDocument) => {
    const confirmed = await confirmDelete()
    if (!confirmed) return
    try {
      await apiClient.deleteKBDocument(kbId, doc.id)
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id))
      setKb((prev) => prev ? { ...prev, document_count: Math.max(0, prev.document_count - 1) } : prev)
      toast.success("Document deleted")
    } catch (err: unknown) {
      toast.error(errorMessage(err, "Failed to delete document"))
    }
  }

  const handleCloseDialog = () => {
    setAddMode(null)
    setTextName("")
    setTextContent("")
    setFileName("")
    setFileData(null)
    setFileFilename(null)
    setFileMediaType(null)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  const handleViewDoc = async (doc: KBDocument) => {
    if (doc.doc_type !== "text") return
    if (doc.content_text) {
      setViewingDoc(doc)
      return
    }

    setViewingDoc(doc)
    setIsLoadingViewingDoc(true)
    try {
      const freshDocs = await apiClient.listKBDocuments(kbId)
      setDocuments(freshDocs)
      setViewingDoc(freshDocs.find((freshDoc) => freshDoc.id === doc.id) ?? doc)
    } catch (err: unknown) {
      toast.error(errorMessage(err, "Failed to load document text"))
    } finally {
      setIsLoadingViewingDoc(false)
    }
  }

  const handleCopyViewingDoc = async () => {
    if (!viewingDoc?.content_text) return
    await navigator.clipboard.writeText(viewingDoc.content_text)
    toast.success("Copied document text")
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!kb) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <p className="text-sm text-muted-foreground">Knowledge base not found.</p>
        <Button variant="outline" size="sm" onClick={() => router.push("/knowledge")}>
          Back to Knowledge Bases
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <ConfirmDialog />

      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-border shrink-0">
        <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => router.push("/knowledge")}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold truncate">{kb.name}</h1>
            {kb.is_shared && (
              <Badge variant="secondary" className="text-xs gap-1 shrink-0">
                <Globe className="h-3 w-3" />
                Shared
              </Badge>
            )}
          </div>
          {kb.description && (
            <p className="text-xs text-muted-foreground truncate">{kb.description}</p>
          )}
        </div>
        {canManageKB && (
          <div className="flex items-center gap-2 shrink-0">
            <Button variant="outline" size="sm" onClick={() => setAddMode("text")}>
              <Type className="h-4 w-4 mr-1.5" />
              Add Text
            </Button>
            <Button size="sm" onClick={() => setAddMode("file")}>
              <Upload className="h-4 w-4 mr-1.5" />
              Upload File
            </Button>
          </div>
        )}
      </div>

      {/* Document list */}
      <div className="flex-1 overflow-y-auto p-6">
        {documents.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-60 gap-3 text-center">
            <FileText className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No documents yet.</p>
            {canManageKB && (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => setAddMode("text")}>
                  <Type className="h-4 w-4 mr-1.5" />
                  Add Text
                </Button>
                <Button size="sm" onClick={() => setAddMode("file")}>
                  <Upload className="h-4 w-4 mr-1.5" />
                  Upload File
                </Button>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {documents.map((doc) => (
              <div
                key={doc.id}
                role={doc.doc_type === "text" ? "button" : undefined}
                tabIndex={doc.doc_type === "text" ? 0 : undefined}
                onClick={() => void handleViewDoc(doc)}
                onKeyDown={(event) => {
                  if (doc.doc_type !== "text") return
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault()
                    void handleViewDoc(doc)
                  }
                }}
                className={`flex items-center gap-3 px-4 py-3 rounded-md border border-border bg-card hover:bg-muted/30 transition-colors ${
                  doc.doc_type === "text" ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" : ""
                }`}
              >
                <div className="shrink-0">
                  {doc.doc_type === "text" ? (
                    <Type className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <FileText className="h-4 w-4 text-muted-foreground" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{doc.name}</p>
                  {doc.filename && (
                    <p className="text-xs text-muted-foreground truncate">{doc.filename}</p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Badge variant={doc.doc_type === "text" ? "outline" : "secondary"} className="text-xs">
                    {doc.doc_type}
                  </Badge>
                  {doc.indexed ? (
                    <div className="flex items-center gap-1 text-xs text-emerald-600">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      <span>Indexed</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="h-3.5 w-3.5" />
                      <span>Pending</span>
                    </div>
                  )}
                  {canManageKB && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-destructive"
                      onClick={(event) => {
                        event.stopPropagation()
                        handleDeleteDoc(doc)
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Text Viewer Dialog */}
      <Dialog open={!!viewingDoc} onOpenChange={(open) => !open && setViewingDoc(null)}>
        <DialogContent className="sm:max-w-4xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="truncate pr-6">{viewingDoc?.name}</DialogTitle>
            <DialogDescription>
              {viewingDoc?.indexed ? "Indexed text document" : "Text document"}
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-border bg-muted/20 px-4 py-3 text-sm">
            {isLoadingViewingDoc ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span>Loading document text...</span>
              </div>
            ) : viewingDoc?.content_text ? (
              <div className="max-w-none [&_pre]:my-2 [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_h1]:text-lg [&_h1]:font-bold [&_h2]:text-base [&_h2]:font-bold [&_h3]:text-sm [&_h3]:font-semibold [&_strong]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground">
                <MarkdownRenderer content={viewingDoc.content_text} />
              </div>
            ) : (
              <p className="text-muted-foreground">No text content stored for this document.</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setViewingDoc(null)}>Close</Button>
            <Button onClick={handleCopyViewingDoc} disabled={isLoadingViewingDoc || !viewingDoc?.content_text}>
              <Copy className="h-4 w-4 mr-2" />
              Copy Text
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add Text Dialog */}
      <Dialog open={addMode === "text"} onOpenChange={(open) => !open && handleCloseDialog()}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
          <DialogHeader className="shrink-0">
            <DialogTitle>Add Text Document</DialogTitle>
            <DialogDescription>
              Paste or type text content. It will be chunked and indexed for RAG.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4 min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="grid gap-2">
              <Label htmlFor="text-name">Name</Label>
              <Input
                id="text-name"
                value={textName}
                onChange={(e) => setTextName(e.target.value)}
                placeholder="Document name"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="text-content">Content</Label>
              <Textarea
                id="text-content"
                value={textContent}
                onChange={(e) => setTextContent(e.target.value)}
                placeholder="Paste your text content here..."
                rows={14}
                className="min-h-72 resize-y font-mono text-xs"
              />
            </div>
          </div>
          <DialogFooter className="shrink-0 border-t border-border pt-4">
            <Button variant="outline" onClick={handleCloseDialog}>Cancel</Button>
            <Button
              onClick={handleAddText}
              disabled={textLoading || !textName.trim() || !textContent.trim()}
            >
              {textLoading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Add & Index
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Upload File Dialog */}
      <Dialog open={addMode === "file"} onOpenChange={(open) => !open && handleCloseDialog()}>
        <DialogContent className="sm:max-w-md max-h-[85vh] flex flex-col">
          <DialogHeader className="shrink-0">
            <DialogTitle>Upload File</DialogTitle>
            <DialogDescription>
              Upload a PDF, DOCX, TXT, or Markdown file. Text will be extracted and indexed.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4 min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="grid gap-2">
              <Label htmlFor="file-input">File</Label>
              <input
                ref={fileInputRef}
                id="file-input"
                type="file"
                accept=".pdf,.docx,.txt,.md,.markdown"
                onChange={handleFileChange}
                className="block w-full text-sm text-muted-foreground file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border file:border-border file:text-xs file:font-medium file:bg-background hover:file:bg-muted cursor-pointer"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="file-name">Display Name</Label>
              <Input
                id="file-name"
                value={fileName}
                onChange={(e) => setFileName(e.target.value)}
                placeholder="Document name"
              />
            </div>
            {fileFilename && (
              <p className="text-xs text-muted-foreground">
                Selected: <span className="font-medium">{fileFilename}</span>
              </p>
            )}
          </div>
          <DialogFooter className="shrink-0 border-t border-border pt-4">
            <Button variant="outline" onClick={handleCloseDialog}>Cancel</Button>
            <Button
              onClick={handleAddFile}
              disabled={fileLoading || !fileName.trim() || !fileData}
            >
              {fileLoading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Upload & Index
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
