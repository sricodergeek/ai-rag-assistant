import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import './App.css'

const API_BASE_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')

function apiUrl(path: string) {
  return import.meta.env.DEV ? path : `${API_BASE_URL}${path}`
}

interface DocumentListItem {
  id: string
  filename: string
  created_at?: string
  chunks_ingested?: number
}

interface ListedDocument {
  id: string
  filename: string
  created_at: string
}

interface ConversationListItem {
  id: string
  created_at: string
  updated_at: string
  title: string
}

interface UploadResponse {
  filename?: string
  document_id?: string
  chunks_ingested?: number
  message?: string
  detail?: string
}

interface AnswerSource {
  source: string
  page: number
}

interface AskResponse {
  answer?: string
  sources?: AnswerSource[]
  detail?: string
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: AnswerSource[]
}

interface AuthUser {
  id: string
  email: string
  name?: string | null
  avatar_url?: string | null
}

function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none">
        <path
          d="M5 18.5V6.75A2.75 2.75 0 0 1 7.75 4h8.5A2.75 2.75 0 0 1 19 6.75v6.5A2.75 2.75 0 0 1 16.25 16H9l-4 2.5Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
        <path d="M9 8.5h6M9 11.5h4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    </span>
  )
}

function UploadIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M10 13V3.75m0 0L6.5 7.25M10 3.75l3.5 3.5M4 12.75v2A1.25 1.25 0 0 0 5.25 16h9.5A1.25 1.25 0 0 0 16 14.75v-2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M3.25 9.9 16.5 3.75l-3.8 12.5-2.45-5.1-7-1.25Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="m10.25 11.15 3.5-3.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function App() {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isAuthLoading, setIsAuthLoading] = useState(true)
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState('')
  const [question, setQuestion] = useState('')
  const [documents, setDocuments] = useState<DocumentListItem[]>([])
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [conversations, setConversations] = useState<ConversationListItem[]>([])
  const [isConversationListLoading, setIsConversationListLoading] = useState(false)
  const [conversationListError, setConversationListError] = useState('')
  const [isHistoryLoading, setIsHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [isAsking, setIsAsking] = useState(false)
  const [askError, setAskError] = useState('')
  const [isUploading, setIsUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [documentListError, setDocumentListError] = useState('')
  const [documentDeleteError, setDocumentDeleteError] = useState('')
  const [deletingDocumentIds, setDeletingDocumentIds] = useState<Set<string>>(
    () => new Set(),
  )
  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const askControllerRef = useRef<AbortController | null>(null)
  const deletingDocumentIdsRef = useRef(new Set<string>())
  const conversationListRequestRef = useRef(0)
  const historyRequestRef = useRef(0)
  const activeDocument = documents.find(
    (document) => document.id === activeDocumentId,
  )

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, isAsking])

  useEffect(() => {
    let isActive = true

    async function checkAuthentication() {
      try {
        const authMeUrl = apiUrl('/auth/me')
        const response = await fetch(authMeUrl, {
          credentials: 'include',
        })
        if (!response.ok) {
          if (isActive) {
            setUser(null)
            setDocuments([])
          }
          return
        }

        const result = (await response.json()) as AuthUser
        if (isActive && typeof result.id === 'string' && typeof result.email === 'string') {
          setUser({
            id: result.id,
            email: result.email,
            name: result.name ?? null,
            avatar_url: result.avatar_url ?? null,
          })

          try {
            const documentsResponse = await fetch(apiUrl('/documents'), {
              credentials: 'include',
            })
            if (!documentsResponse.ok) {
              throw new Error('Document list request failed.')
            }

            const listedDocuments = (await documentsResponse.json()) as ListedDocument[]
            if (!Array.isArray(listedDocuments)) {
              throw new Error('Invalid document list response.')
            }

            const documentItems = listedDocuments.filter(
              (document) =>
                typeof document.id === 'string' &&
                typeof document.filename === 'string' &&
                typeof document.created_at === 'string',
            )
            if (isActive) {
              setDocuments(documentItems)
              setDocumentListError('')
            }
          } catch {
            if (isActive) {
              setDocuments([])
              setDocumentListError('Could not load your documents. Please refresh to try again.')
            }
          }
        }
      } catch {
        if (isActive) {
          setUser(null)
          setDocuments([])
        }
      } finally {
        if (isActive) setIsAuthLoading(false)
      }
    }

    void checkAuthentication()
    return () => {
      isActive = false
    }
  }, [])

  async function handleLogout() {
    if (isLoggingOut) return
    setIsLoggingOut(true)
    setLogoutError('')

    try {
      const logoutUrl = apiUrl('/auth/logout')
      const response = await fetch(logoutUrl, {
        method: 'POST',
        credentials: 'include',
      })
      if (!response.ok) {
        throw new Error('Logout failed.')
      }
      setUser(null)
      setDocuments([])
      setActiveDocumentId(null)
      setConversationId(null)
      setConversations([])
      setConversationListError('')
      setHistoryError('')
      setIsConversationListLoading(false)
      setIsHistoryLoading(false)
      conversationListRequestRef.current += 1
      historyRequestRef.current += 1
      askControllerRef.current?.abort()
      askControllerRef.current = null
      setIsAsking(false)
      setMessages([])
      setQuestion('')
      setAskError('')
      setDocumentListError('')
      setDocumentDeleteError('')
      setUploadMessage('')
      setUploadError('')
    } catch {
      setLogoutError('Could not log out. Please try again.')
    } finally {
      setIsLoggingOut(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submittedQuestion = question.trim()
    if (!submittedQuestion || isAsking) return

    if (!activeDocumentId || !activeDocument) {
      setAskError('Upload a PDF first.')
      return
    }

    const submittedDocumentId = activeDocumentId
    const submittedConversationId = conversationId ?? crypto.randomUUID()
    setConversationId(submittedConversationId)

    setAskError('')
    setQuestion('')
    setMessages((currentMessages) => [
      ...currentMessages,
      {
        id: crypto.randomUUID(),
        role: 'user',
        content: submittedQuestion,
      },
    ])
    setIsAsking(true)
    const controller = new AbortController()
    askControllerRef.current = controller

    try {
      const askUrl = apiUrl('/ask')
      const response = await fetch(askUrl, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          question: submittedQuestion,
          conversation_id: submittedConversationId,
          document_id: submittedDocumentId,
        }),
      })
      const result = (await response.json().catch(() => ({}))) as AskResponse
      await loadConversations(submittedDocumentId)

      if (!response.ok) {
        const message = response.status < 500 && result.detail
          ? result.detail
          : 'Your question could not be answered. Please try again.'
        throw new Error(message)
      }

      if (typeof result.answer !== 'string') {
        throw new Error('The server returned an unexpected answer.')
      }

      setMessages((currentMessages) => [
        ...currentMessages,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: result.answer as string,
          sources: result.sources ?? [],
        },
      ])
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') return
      setAskError(
        error instanceof TypeError
          ? 'Could not connect to the assistant. Please try again.'
          : error instanceof Error
            ? error.message
            : 'Your question could not be answered. Please try again.',
      )
    } finally {
      if (askControllerRef.current === controller) {
        askControllerRef.current = null
        setIsAsking(false)
      }
    }
  }

  function startConversationForDocument(documentId: string) {
    historyRequestRef.current += 1
    askControllerRef.current?.abort()
    askControllerRef.current = null
    setIsAsking(false)
    setActiveDocumentId(documentId)
    setConversationId(null)
    setMessages([])
    setQuestion('')
    setAskError('')
    setConversations([])
    setHistoryError('')
    setIsHistoryLoading(false)
    void loadConversations(documentId)
  }

  async function loadConversations(documentId: string) {
    const requestId = ++conversationListRequestRef.current
    setIsConversationListLoading(true)
    setConversationListError('')

    try {
      const response = await fetch(
        apiUrl(`/documents/${encodeURIComponent(documentId)}/conversations`),
        { credentials: 'include' },
      )
      if (!response.ok) throw new Error('Conversation list request failed.')

      const result: unknown = await response.json()
      if (!Array.isArray(result)) throw new Error('Invalid conversation list.')
      const validConversations = result.filter(
        (item): item is ConversationListItem =>
          item !== null &&
          typeof item === 'object' &&
          typeof item.id === 'string' &&
          typeof item.title === 'string' &&
          typeof item.created_at === 'string' &&
          typeof item.updated_at === 'string',
      )
      if (validConversations.length !== result.length) {
        throw new Error('Invalid conversation list item.')
      }
      if (requestId === conversationListRequestRef.current) {
        setConversations(validConversations)
      }
    } catch {
      if (requestId === conversationListRequestRef.current) {
        setConversations([])
        setConversationListError('Could not load conversations. Please try again.')
      }
    } finally {
      if (requestId === conversationListRequestRef.current) {
        setIsConversationListLoading(false)
      }
    }
  }

  function startNewConversation() {
    historyRequestRef.current += 1
    askControllerRef.current?.abort()
    askControllerRef.current = null
    setIsAsking(false)
    setConversationId(null)
    setMessages([])
    setQuestion('')
    setAskError('')
    setHistoryError('')
    setIsHistoryLoading(false)
  }

  async function selectConversation(documentId: string, selectedConversationId: string) {
    const requestId = ++historyRequestRef.current
    askControllerRef.current?.abort()
    askControllerRef.current = null
    setIsAsking(false)
    setConversationId(selectedConversationId)
    setMessages([])
    setQuestion('')
    setAskError('')
    setHistoryError('')
    setIsHistoryLoading(true)

    try {
      const response = await fetch(
        apiUrl(
          `/documents/${encodeURIComponent(documentId)}/conversations/${encodeURIComponent(selectedConversationId)}/messages`,
        ),
        { credentials: 'include' },
      )
      if (!response.ok) throw new Error('Conversation history request failed.')

      const result: unknown = await response.json()
      if (!Array.isArray(result)) throw new Error('Invalid conversation history.')
      const restoredMessages = result.filter(
        (item): item is ChatMessage =>
          item !== null &&
          typeof item === 'object' &&
          typeof item.id === 'string' &&
          (item.role === 'user' || item.role === 'assistant') &&
          typeof item.content === 'string',
      )
      if (restoredMessages.length !== result.length) {
        throw new Error('Invalid conversation history item.')
      }
      if (requestId === historyRequestRef.current) {
        setMessages(restoredMessages)
      }
    } catch {
      if (requestId === historyRequestRef.current) {
        setHistoryError('Could not load this conversation. Please try again.')
      }
    } finally {
      if (requestId === historyRequestRef.current) setIsHistoryLoading(false)
    }
  }

  function handleQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  async function uploadPdf(file: File) {
    setIsUploading(true)
    setUploadMessage('')
    setUploadError('')

    const formData = new FormData()
    formData.append('file', file)

    try {
      const uploadUrl = apiUrl('/upload')
      const response = await fetch(uploadUrl, {
        method: 'POST',
        credentials: 'include',
        body: formData,
      })
      const result = (await response.json().catch(() => ({}))) as UploadResponse

      if (!response.ok) {
        const message = response.status < 500 && result.detail
          ? result.detail
          : 'The PDF could not be uploaded. Please try again.'
        throw new Error(message)
      }

      if (
        !result.filename ||
        !result.document_id ||
        typeof result.chunks_ingested !== 'number'
      ) {
        throw new Error('The server returned an unexpected upload response.')
      }

      const uploadedDocument: DocumentListItem = {
        id: result.document_id,
        filename: result.filename,
        chunks_ingested: result.chunks_ingested,
      }
      setDocuments((currentDocuments) => [uploadedDocument, ...currentDocuments])
      setDocumentListError('')
      startConversationForDocument(uploadedDocument.id)
      setUploadMessage(
        `${result.filename} uploaded successfully · ${result.chunks_ingested} chunks ingested.`,
      )
    } catch (error) {
      setUploadError(
        error instanceof TypeError
          ? 'Could not connect to the upload service. Please try again.'
          : error instanceof Error
            ? error.message
            : 'The PDF could not be uploaded. Please try again.',
      )
    } finally {
      setIsUploading(false)
    }
  }

  async function deleteDocument(documentId: string) {
    if (deletingDocumentIdsRef.current.has(documentId)) return

    deletingDocumentIdsRef.current.add(documentId)
    setDeletingDocumentIds((current) => new Set(current).add(documentId))
    setDocumentDeleteError('')

    try {
      const response = await fetch(apiUrl(`/documents/${encodeURIComponent(documentId)}`), {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!response.ok) {
        throw new Error('Document deletion failed.')
      }

      setDocuments((current) => current.filter((document) => document.id !== documentId))
      if (activeDocumentId === documentId) {
        conversationListRequestRef.current += 1
        historyRequestRef.current += 1
        askControllerRef.current?.abort()
        askControllerRef.current = null
        setIsAsking(false)
        setActiveDocumentId(null)
        setConversationId(null)
        setMessages([])
        setQuestion('')
        setAskError('')
        setConversations([])
        setConversationListError('')
        setHistoryError('')
        setIsConversationListLoading(false)
        setIsHistoryLoading(false)
      }
    } catch {
      setDocumentDeleteError('Could not delete this document. Please try again.')
    } finally {
      deletingDocumentIdsRef.current.delete(documentId)
      setDeletingDocumentIds((current) => {
        const next = new Set(current)
        next.delete(documentId)
        return next
      })
    }
  }

  function handleFileSelection(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''

    if (!file) return

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setUploadMessage('')
      setUploadError('Please choose a PDF file.')
      return
    }

    void uploadPdf(file)
  }

  if (isAuthLoading) {
    return (
      <main className="auth-screen">
        <section className="auth-card" role="status" aria-live="polite">
          <div className="brand auth-brand">
            <BrandMark />
            <span>AI Knowledge Assistant</span>
          </div>
          <span className="loading-spinner auth-spinner" aria-hidden="true" />
          <p className="auth-copy">Checking your sign-in…</p>
        </section>
      </main>
    )
  }

  if (!user) {
    return (
      <main className="auth-screen">
        <section className="auth-card">
          <div className="brand auth-brand">
            <BrandMark />
            <span>AI Knowledge Assistant</span>
          </div>
          <p className="eyebrow welcome-eyebrow auth-eyebrow">YOUR DOCUMENTS, UNDERSTOOD</p>
          <h1 className="auth-title">Your documents, ready for answers</h1>
          <p className="auth-copy">
            Sign in to upload documents and ask questions grounded in their contents.
          </p>
          <button
            className="google-login-button auth-login-button"
            type="button"
            onClick={() => {
              window.location.href = `${API_BASE_URL}/auth/google`
            }}
          >
            Continue with Google
          </button>
        </section>
      </main>
    )
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#home" aria-label="AI Knowledge Assistant home">
          <BrandMark />
          <span>AI Knowledge Assistant</span>
        </a>
        <div className="topbar-auth">
          <div className="topbar-status">
            <span className="status-dot" />
            <span>Logged in</span>
          </div>
          <span className="topbar-user-email" title={user.email}>{user.email}</span>
          <button
            className="logout-button"
            type="button"
            onClick={() => void handleLogout()}
            disabled={isLoggingOut}
          >
            {isLoggingOut ? 'Logging out…' : 'Logout'}
          </button>
          {logoutError && <span className="logout-error" role="alert">{logoutError}</span>}
        </div>
      </header>

      <div className="workspace">
        <aside className="document-sidebar" aria-label="Documents sidebar">
          <div className="sidebar-heading">
            <div>
              <p className="eyebrow">YOUR WORKSPACE</p>
              <h2>Documents</h2>
            </div>
            <span className="document-count">{documents.length}</span>
          </div>

          <button
            className="upload-button"
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
          >
            <UploadIcon />
            <span>{isUploading ? 'Uploading…' : 'Upload PDF'}</span>
          </button>
          <input
            ref={fileInputRef}
            className="visually-hidden"
            type="file"
            accept=".pdf,application/pdf"
            onChange={handleFileSelection}
            tabIndex={-1}
          />

          {isUploading && (
            <p className="upload-feedback upload-loading" role="status">
              <span className="loading-spinner" aria-hidden="true" />
              Uploading and processing PDF…
            </p>
          )}
          {uploadMessage && (
            <p className="upload-feedback upload-success" role="status">
              {uploadMessage}
            </p>
          )}
          {uploadError && (
            <p className="upload-feedback upload-error" role="alert">
              {uploadError}
            </p>
          )}

          {documentDeleteError && (
            <p className="upload-feedback upload-error" role="alert">
              {documentDeleteError}
            </p>
          )}

          {documents.length > 0 ? (
            <ul className="document-list" aria-label="Uploaded documents">
              {documents.map((document) => (
                <li className="document-list-item" key={document.id}>
                  <button
                    className={`document-item${activeDocumentId === document.id ? ' active' : ''}`}
                    type="button"
                    aria-pressed={activeDocumentId === document.id}
                    onClick={() => {
                      if (activeDocumentId !== document.id) {
                        startConversationForDocument(document.id)
                      }
                    }}
                  >
                    <span className="pdf-file-icon" aria-hidden="true">PDF</span>
                    <span className="document-item-copy">
                      <span className="document-name" title={document.filename}>
                        {document.filename}
                      </span>
                      {typeof document.chunks_ingested === 'number' && (
                        <span className="document-chunks">
                          {document.chunks_ingested} chunks
                        </span>
                      )}
                    </span>
                  </button>
                  <button
                    className="document-delete-button"
                    type="button"
                    aria-label={`Delete ${document.filename}`}
                    title={`Delete ${document.filename}`}
                    disabled={deletingDocumentIds.has(document.id)}
                    onClick={() => void deleteDocument(document.id)}
                  >
                    {deletingDocumentIds.has(document.id) ? 'Deleting…' : 'Delete'}
                  </button>
                </li>
              ))}
            </ul>
          ) : documentListError ? (
            <p className="upload-feedback upload-error" role="alert">
              {documentListError}
            </p>
          ) : (
            <div className="documents-empty">
              <div className="empty-file-icon" aria-hidden="true">
                <svg viewBox="0 0 32 32" fill="none">
                  <path d="M9 4.75h8.5L24 11v15.25A1.75 1.75 0 0 1 22.25 28h-12.5A1.75 1.75 0 0 1 8 26.25V6.5a1.75 1.75 0 0 1 1-1.75Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                  <path d="M17 5v6h6M12 17h8M12 21h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <p className="empty-title">No documents yet</p>
              <p className="empty-copy">Upload a PDF to start asking questions about its contents.</p>
            </div>
          )}

          {activeDocumentId && (
            <section className="conversation-panel" aria-label="Conversations">
              <div className="conversation-heading">
                <h3>Conversations</h3>
                <button
                  className="new-conversation-button"
                  type="button"
                  onClick={startNewConversation}
                >
                  New conversation
                </button>
              </div>
              {isConversationListLoading ? (
                <p className="conversation-feedback" role="status">Loading conversations…</p>
              ) : conversationListError ? (
                <p className="conversation-feedback conversation-error" role="alert">
                  {conversationListError}
                </p>
              ) : conversations.length > 0 ? (
                <ul className="conversation-list">
                  {conversations.map((conversation) => (
                    <li key={conversation.id}>
                      <button
                        className={`conversation-item${conversationId === conversation.id ? ' active' : ''}`}
                        type="button"
                        aria-pressed={conversationId === conversation.id}
                        onClick={() => void selectConversation(activeDocumentId, conversation.id)}
                      >
                        <span>{conversation.title}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="conversation-feedback">No conversations yet.</p>
              )}
            </section>
          )}

          <div className="sidebar-footer">
            <span className="privacy-icon" aria-hidden="true">✳</span>
            <span>Your documents stay yours</span>
          </div>
        </aside>

        <main className="chat-area">
          <div className="chat-document-indicator">
            {activeDocument ? (
              <>
                <span>Chatting with:</span>
                <strong title={activeDocument.filename}>{activeDocument.filename}</strong>
              </>
            ) : (
              <span>Select a document to start chatting</span>
            )}
          </div>
          <section
            className={`chat-content${messages.length > 0 ? ' has-messages' : ''}`}
            aria-live="polite"
          >
            {isHistoryLoading ? (
              <p className="conversation-history-status" role="status">
                <span className="loading-spinner" aria-hidden="true" />
                Loading conversation…
              </p>
            ) : historyError ? (
              <p className="conversation-history-error" role="alert">{historyError}</p>
            ) : messages.length === 0 ? (
              <div className="welcome-block">
                <div className="welcome-icon" aria-hidden="true">
                  <svg viewBox="0 0 44 44" fill="none">
                    <path d="M22 5.5 25.4 17l11.1 4.2-11.1 4.2L22 37l-4.1-11.6L6.5 21.2 17.9 17 22 5.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
                    <path d="m34.5 5 .9 3.1 3.1 1-3.1 1.1-.9 3.1-1.1-3.1-3.1-1.1 3.1-1L34.5 5Z" fill="currentColor" />
                  </svg>
                </div>
                <p className="eyebrow welcome-eyebrow">YOUR DOCUMENTS, UNDERSTOOD</p>
                <h1>Ask questions about your documents</h1>
                <p className="welcome-copy">Get clear answers grounded in the information you share.</p>
              </div>
            ) : (
              <div className="message-list">
                {messages.map((message) => (
                  <article className={`message-row ${message.role}`} key={message.id}>
                    {message.role === 'assistant' && (
                      <span className="message-avatar assistant-avatar" aria-hidden="true">
                        <BrandMark />
                      </span>
                    )}
                    <div className="message-content">
                      <p className="message-author">
                        {message.role === 'user' ? 'You' : 'Assistant'}
                      </p>
                      <div className="message-bubble">
                        <p>{message.content}</p>
                      </div>
                      {message.sources && message.sources.length > 0 && (
                        <div className="answer-sources">
                          <span className="sources-label">Sources</span>
                          <ul>
                            {message.sources.map((source, index) => (
                              <li key={`${source.source}-${source.page}-${index}`}>
                                <span>{source.source}</span>
                                <span className="source-page">Page {source.page}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </article>
                ))}
                {isAsking && (
                  <article className="message-row assistant" role="status">
                    <span className="message-avatar assistant-avatar" aria-hidden="true">
                      <BrandMark />
                    </span>
                    <div className="message-content">
                      <p className="message-author">Assistant</p>
                      <div className="message-bubble thinking-bubble">
                        <span className="loading-spinner" aria-hidden="true" />
                        <span>Thinking…</span>
                      </div>
                    </div>
                  </article>
                )}
                <div ref={messagesEndRef} />
              </div>
            )}
          </section>

          <div className="composer-wrap">
            {askError && <p className="ask-error" role="alert">{askError}</p>}
            <form className="composer" onSubmit={handleSubmit}>
              <label className="visually-hidden" htmlFor="question-input">Ask a question</label>
              <textarea
                id="question-input"
                placeholder="Ask a question..."
                rows={1}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={handleQuestionKeyDown}
              />
              <button
                className="send-button"
                type="submit"
                aria-label="Send question"
                disabled={!question.trim() || isAsking || !activeDocumentId}
              >
                <SendIcon />
              </button>
            </form>
            <p className="composer-note">Answers are generated from your uploaded documents.</p>
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
