import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Check,
  Download,
  FileText,
  Loader2,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

type FactStatus = 'pending' | 'accepted' | 'rejected'
type SourceKind = 'auto' | 'resume' | 'technical_document' | 'portfolio'

const sourceKindLabels: Record<SourceKind, string> = {
  auto: '自动识别',
  resume: '简历',
  technical_document: '技术文档',
  portfolio: '作品集',
}

const clarificationKindLabels: Record<string, string> = {
  ownership: '确认个人贡献',
  conflict: '核对两条不一致信息',
  missing_result: '补充可验证结果',
  missing_task: '补充个人任务',
  missing_situation: '补充背景约束',
  metric_source: '确认量化口径',
  derived_skill: '确认专业能力',
}
interface ResumeSource {
  id: string
  filename: string
  source_type: string
  detected_kind: string
  detected_kind_confidence: number
  detected_kind_evidence?: string | null
  selected_kind?: string | null
  status: string
  error?: string | null
  fact_count: number
  accepted_count: number
  pending_count: number
  created_at: string
}

interface StarComponent {
  text: string
  evidence: string
}

interface StructuredFact {
  document_kind?: string
  value?: string
  title?: string
  situation?: StarComponent
  task?: StarComponent
  action?: StarComponent
  result?: StarComponent
  technologies?: Array<{ name: string; evidence: string }>
  professional_skills?: Array<{ name: string; evidence: string; derived: boolean }>
  ownership_level?: string
  missing_fields?: string[]
}

interface ResumeFact {
  id: string
  source_id: string
  source_filename: string
  category: string
  content: string
  effective_content: string
  evidence: string
  confidence: number
  fact_type: string
  entity_type?: string | null
  field_name?: string | null
  completeness: number
  needs_clarification: boolean
  structured_data?: StructuredFact | null
  status: FactStatus
}

interface ResumeVersion {
  id: string
  name: string
  target_role?: string
  markdown: string
  file_path: string
  status: 'draft' | 'active'
  fact_count: number
  created_at: string
}

interface ClarificationConflictOption {
  label: string
  value: string
  sources: string[]
  details: string[]
}

interface Clarification {
  id: string
  fact_id?: string | null
  kind: string
  question: string
  priority: number
  answer?: string | null
  status: 'open' | 'answered' | 'dismissed'
  source_filename?: string | null
  fact_content?: string | null
  metadata?: {
    context?: string
    answer_guidance?: string
    resume_effect?: string
    entity_label?: string
    field_label?: string
    conflict_options?: ClarificationConflictOption[]
  }
}

interface ProfileVersion {
  id: string
  name: string
  markdown: string
  status: 'draft' | 'active'
  fact_count: number
  clarification_count: number
  profile_json?: {
    known_gaps?: Array<{ text: string }>
    approved_framings?: Array<{ text: string }>
  }
  quality_report: {
    accepted_fact_count?: number
    used_fact_count?: number
    open_clarification_count?: number
    incomplete_fact_count?: number
    evidence_coverage?: number
    validation_warning_count?: number
    target_role?: string
  }
  created_at: string
}

interface Workspace {
  sources: ResumeSource[]
  facts: ResumeFact[]
  versions: ResumeVersion[]
  clarifications: Clarification[]
  profile_versions: ProfileVersion[]
}

const emptyWorkspace: Workspace = {
  sources: [],
  facts: [],
  versions: [],
  clarifications: [],
  profile_versions: [],
}

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || '操作失败')
  return data as T
}

export default function ResumeStudioPage() {
  const [workspace, setWorkspace] = useState<Workspace>(emptyWorkspace)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [sourceKinds, setSourceKinds] = useState<Record<string, SourceKind>>({})
  const [factFilter, setFactFilter] = useState<FactStatus>('pending')
  const [clarificationDrafts, setClarificationDrafts] = useState<Record<string, string>>({})
  const [targetRole, setTargetRole] = useState('')

  const loadWorkspace = useCallback(async () => {
    try {
      setWorkspace(await api<Workspace>('/api/resume-studio'))
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : '简历工作室加载失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadWorkspace()
  }, [loadWorkspace])

  const acceptedCount = workspace.facts.filter(fact => fact.status === 'accepted').length
  const visibleFacts = useMemo(
    () => workspace.facts.filter(fact => fact.status === factFilter),
    [factFilter, workspace.facts],
  )
  const latestProfile = workspace.profile_versions[0]
  const openClarifications = workspace.clarifications.filter(item => item.status === 'open')
  const answeredClarifications = workspace.clarifications.filter(item => item.status === 'answered')

  const run = async (
    key: string,
    action: () => Promise<string>,
    options: { refresh?: boolean } = {},
  ) => {
    setBusy(key)
    try {
      setMessage({ ok: true, text: await action() })
      if (options.refresh !== false) await loadWorkspace()
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : '操作失败' })
    } finally {
      setBusy('')
    }
  }

  const replaceWorkspaceFact = (updatedFact: ResumeFact) => {
    setWorkspace(current => {
      const facts = current.facts.map(fact => fact.id === updatedFact.id ? updatedFact : fact)
      const sources = current.sources.map(source => {
        if (source.id !== updatedFact.source_id) return source
        const sourceFacts = facts.filter(fact => fact.source_id === source.id)
        return {
          ...source,
          fact_count: sourceFacts.length,
          accepted_count: sourceFacts.filter(fact => fact.status === 'accepted').length,
          pending_count: sourceFacts.filter(fact => fact.status === 'pending').length,
        }
      })
      return { ...current, facts, sources }
    })
  }

  const uploadSources = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    if (!files.length) return
    await run('upload', async () => {
      let duplicates = 0
      for (const file of files) {
        const form = new FormData()
        form.append('file', file)
        const result = await api<{ duplicate: boolean }>('/api/resume-studio/sources', {
          method: 'POST',
          body: form,
        })
        if (result.duplicate) duplicates += 1
      }
      return `已处理 ${files.length} 份材料${duplicates ? `，其中 ${duplicates} 份为重复材料` : ''}`
    })
    event.target.value = ''
  }

  const confirmExternalAI = (detail: string) => window.confirm(
    '外部 AI 数据发送确认\n\n' + detail
    + '\n\n这些内容可能包含姓名、联系方式、经历和私有项目资料，并将发送到当前配置的外部 AI 服务。'
    + '\n点击“确定”表示你知情并同意本次发送；点击“取消”不会发送。',
  )

  const extractFacts = (source: ResumeSource) => {
    if (!confirmExternalAI('将发送材料「' + source.filename + '」的相关完整文本用于事实与 STAR 提取。')) return
    return run(`extract:${source.id}`, async () => {
    const sourceKind = sourceKinds[source.id] ?? (source.selected_kind as SourceKind | undefined) ?? 'auto'
    const result = await api<{ facts: ResumeFact[] }>(`/api/resume-studio/sources/${source.id}/extract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_kind: sourceKind, external_ai_consent: true }),
    })
    setFactFilter('pending')
    return `已从「${source.filename}」提取 ${result.facts.length} 条待审核事实`
    })
  }

  const deleteSource = (source: ResumeSource) => {
    if (!window.confirm(`确认删除材料「${source.filename}」及其未引用事实？`)) return
    return run(`delete:${source.id}`, async () => {
      await api(`/api/resume-studio/sources/${source.id}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed: true }),
      })
      setWorkspace(current => ({
        ...current,
        sources: current.sources.filter(item => item.id !== source.id),
        facts: current.facts.filter(fact => fact.source_id !== source.id),
      }))
      return `已删除材料「${source.filename}」`
    }, { refresh: false })
  }

  const reviewFact = (fact: ResumeFact, status: FactStatus) => run(`fact:${fact.id}`, async () => {
    const result = await api<{ fact: ResumeFact }>(`/api/resume-studio/facts/${fact.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, content: drafts[fact.id] ?? fact.effective_content }),
    })
    replaceWorkspaceFact(result.fact)
    setDrafts(current => {
      const next = { ...current }
      delete next[fact.id]
      return next
    })
    return status === 'accepted' ? '事实已接受并进入生成范围' : status === 'rejected' ? '事实已拒绝' : '事实已退回待审核'
  }, { refresh: false })

  const refreshClarifications = () => run('refresh-clarifications', async () => {
    const result = await api<{ clarifications: Clarification[] }>(
      '/api/resume-studio/profile/clarifications/refresh',
      { method: 'POST' },
    )
    setWorkspace(current => ({ ...current, clarifications: result.clarifications }))
    return '已整理 ' + result.clarifications.filter(item => item.status === 'open').length + ' 个待确认问题'
  }, { refresh: false })

  const updateClarification = (item: Clarification, status: 'answered' | 'dismissed') =>
    run('clarification:' + item.id, async () => {
      const result = await api<{ clarification: Clarification }>('/api/resume-studio/profile/clarifications/' + item.id, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, answer: clarificationDrafts[item.id] || '' }),
      })
      setWorkspace(current => ({
        ...current,
        clarifications: current.clarifications.map(existing => (
          existing.id === item.id ? result.clarification : existing
        )),
      }))
      setClarificationDrafts(current => {
        const next = { ...current }
        delete next[item.id]
        return next
      })
      return status === 'answered' ? '补充信息已确认' : '该问题已忽略'
    }, { refresh: false })

  const composeProfile = () => {
    if (!confirmExternalAI('将发送所有已接受事实和已确认回答，用于生成按项目分组的多 STAR 职业简历档案。')) return
    return run('compose-profile', async () => {
    const result = await api<{ profile: ProfileVersion }>('/api/resume-studio/profile/compose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ external_ai_consent: true, target_role: targetRole.trim() }),
    })
    setWorkspace(current => ({
      ...current,
      profile_versions: [
        result.profile,
        ...current.profile_versions.filter(profile => profile.id !== result.profile.id),
      ],
    }))
    return '已生成「' + result.profile.name + '」，请检查事实覆盖率和正文'
    }, { refresh: false })
  }

  const activateProfile = (profile: ProfileVersion) => run('activate-profile:' + profile.id, async () => {
    const result = await api<{ profile: ProfileVersion }>(
      '/api/resume-studio/profile/versions/' + profile.id + '/activate',
      { method: 'POST' },
    )
    setWorkspace(current => ({
      ...current,
      profile_versions: current.profile_versions.map(existing => ({
        ...existing,
        status: existing.id === result.profile.id ? 'active' : 'draft',
      })),
    }))
    return '已启用「' + profile.name + '」作为当前求职主简历'
  }, { refresh: false })

  const clearWorkspace = () => {
    const confirmation = window.prompt(
      '此操作会永久删除简历工作室上传材料、审核事实、确认问题和生成版本。\n不会删除 resume_markdown 中的原始简历。\n\n请输入“清空”继续：',
    )
    if (confirmation !== '清空') return
    return run('clear-workspace', async () => {
      const result = await api<{ deleted_files: number }>('/api/resume-studio', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed: true, confirmation_text: '清空' }),
      })
      setDrafts({})
      setClarificationDrafts({})
      setSourceKinds({})
      setWorkspace(emptyWorkspace)
      return '已清空简历工作室，并删除 ' + result.deleted_files + ' 个受管文件'
    }, { refresh: false })
  }

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">加载简历工作室...</div>
  }

  return (
    <div className="space-y-5 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-black text-foreground">简历工作室</h1>
          <p className="mt-1 text-sm text-muted">
            从简历、技术文档和作品集提取证据，按“一个项目、多个 STAR”生成可直接启用的主简历。
          </p>
        </div>
        <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={clearWorkspace}>
          {busy === 'clear-workspace' ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Trash2 className="mr-1 h-3 w-3" />}
          清空工作室
        </Button>
      </div>

      {message && (
        <div className={`fixed right-6 top-6 z-50 max-w-md rounded-xl px-4 py-3 text-sm shadow-lg ${message.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
          {message.text}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(320px,0.8fr)_minmax(520px,1.2fr)]">
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>1. 添加个人材料</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <label className="flex cursor-pointer flex-col items-center rounded-xl border-2 border-dashed border-card-border p-6 hover:border-primary/50 hover:bg-[#FFFCFA]">
                {busy === 'upload' ? <Loader2 className="mb-2 h-6 w-6 animate-spin text-primary" /> : <Upload className="mb-2 h-6 w-6 text-muted" />}
                <span className="text-sm font-bold">批量上传简历、技术文档或作品集</span>
                <span className="mt-1 text-xs text-muted">支持 .md、.docx、带文字层的 .pdf；单文件不超过 10MB</span>
                <input
                  className="hidden"
                  type="file"
                  multiple
                  disabled={Boolean(busy)}
                  accept=".md,.docx,.pdf,application/pdf"
                  onChange={uploadSources}
                />
              </label>

              {workspace.sources.length === 0 ? (
                <p className="py-4 text-center text-xs text-muted">尚未添加材料</p>
              ) : workspace.sources.map(source => (
                <div key={source.id} className="rounded-xl border border-card-border p-3">
                  <div className="flex items-start gap-3">
                    <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-bold">{source.filename}</p>
                      <p className="mt-1 text-xs text-muted">
                        {source.fact_count || 0} 条事实 · {source.accepted_count || 0} 条已接受
                      </p>
                      {source.detected_kind && source.detected_kind !== 'unknown' && (
                        <p className="mt-1 text-xs text-muted">
                          已识别为 {sourceKindLabels[source.detected_kind as SourceKind] || source.detected_kind}
                          {' · '}{Math.round((source.detected_kind_confidence || 0) * 100)}%
                        </p>
                      )}
                      {source.error && <p className="mt-2 text-xs text-red-500">{source.error}</p>}
                    </div>
                    <button
                      className="text-muted hover:text-red-500"
                      title="删除材料"
                      disabled={Boolean(busy)}
                      onClick={() => deleteSource(source)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                  <label className="mt-3 block text-xs text-muted">
                    材料类型
                    <select
                      className="mt-1 w-full rounded-lg border border-card-border bg-white px-3 py-2 text-sm outline-none focus:border-primary"
                      value={sourceKinds[source.id] ?? (source.selected_kind as SourceKind | undefined) ?? 'auto'}
                      disabled={Boolean(busy)}
                      onChange={event => setSourceKinds(current => ({
                        ...current,
                        [source.id]: event.target.value as SourceKind,
                      }))}
                    >
                      {(Object.keys(sourceKindLabels) as SourceKind[]).map(kind => (
                        <option key={kind} value={kind}>{sourceKindLabels[kind]}</option>
                      ))}
                    </select>
                  </label>
                  <Button
                    className="mt-3 w-full"
                    variant="secondary"
                    size="sm"
                    disabled={Boolean(busy)}
                    onClick={() => extractFacts(source)}
                  >
                    {busy === `extract:${source.id}` ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Sparkles className="mr-1 h-3 w-3" />}
                    {source.fact_count ? '重新提取待审核事实' : '提取事实'}
                  </Button>
                </div>
              ))}
              <p className="rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800">
                上传只保存在本地。每次点击“提取事实”前都会再次说明发送内容并征求同意；不同意时不会调用外部 AI。
              </p>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle>2. 审核材料事实</CardTitle>
                <div className="flex gap-1">
                  {(['pending', 'accepted', 'rejected'] as FactStatus[]).map(status => (
                    <Button
                      key={status}
                      variant={factFilter === status ? 'default' : 'ghost'}
                      size="sm"
                      onClick={() => setFactFilter(status)}
                    >
                      {status === 'pending' ? '待审核' : status === 'accepted' ? '已接受' : '已拒绝'}
                      （{workspace.facts.filter(fact => fact.status === status).length}）
                    </Button>
                  ))}
                </div>
              </div>
            </CardHeader>
            <CardContent className="h-[720px] space-y-3 overflow-y-auto pr-2">
              {visibleFacts.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted">该分组暂无事实</p>
              ) : visibleFacts.map(fact => (
                <div key={fact.id} className="rounded-xl border border-card-border p-4">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="rounded-full bg-[#FFF0E5] px-2 py-1 text-xs font-bold text-primary">{fact.category}</span>
                    <span className="truncate text-xs text-muted">来源：{fact.source_filename}</span>
                  </div>
                  <textarea
                    className="min-h-20 w-full rounded-lg border border-card-border bg-white p-3 text-sm outline-none focus:border-primary"
                    value={drafts[fact.id] ?? fact.effective_content}
                    onChange={event => setDrafts(current => ({ ...current, [fact.id]: event.target.value }))}
                    maxLength={1000}
                  />
                  <div className="mt-2 rounded-lg bg-[#FFFCFA] p-3">
                    <p className="text-[11px] font-bold text-muted">原文证据 · 置信度 {Math.round(fact.confidence * 100)}%</p>
                    <p className="mt-1 text-xs leading-5 text-muted">{fact.evidence}</p>
                  </div>
                  {fact.fact_type === 'star_story' && fact.structured_data && (
                    <div className="mt-2 space-y-2 rounded-lg border border-card-border p-3">
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <span className="font-bold">{fact.structured_data.title || '未命名项目'}</span>
                        <span className="text-muted">STAR 完整度 {Math.round((fact.completeness || 0) * 100)}%</span>
                        <span className="text-muted">贡献边界：{fact.structured_data.ownership_level || 'unknown'}</span>
                      </div>
                      {(['situation', 'task', 'action', 'result'] as const).map(component => {
                        const value = fact.structured_data?.[component]
                        return value ? (
                          <div key={component} className="grid gap-1 text-xs sm:grid-cols-[24px_1fr]">
                            <strong className="uppercase text-primary">{component[0]}</strong>
                            <span>{value.text}</span>
                          </div>
                        ) : null
                      })}
                      {Boolean(fact.structured_data.technologies?.length) && (
                        <p className="text-xs text-muted">
                          技术：{fact.structured_data.technologies?.map(item => item.name).join('、')}
                        </p>
                      )}
                      {Boolean(fact.structured_data.professional_skills?.length) && (
                        <p className="text-xs text-muted">
                          待确认专业技能：{fact.structured_data.professional_skills?.map(item => item.name).join('、')}
                        </p>
                      )}
                      {fact.needs_clarification && (
                        <p className="text-xs font-medium text-amber-700">
                          需要补充确认：{fact.structured_data.missing_fields?.join('、') || '个人贡献或结果'}
                        </p>
                      )}
                    </div>
                  )}
                  <div className="mt-3 flex justify-end gap-2">
                    {fact.status !== 'rejected' && (
                      <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => reviewFact(fact, 'rejected')}>
                        <X className="mr-1 h-3 w-3" />拒绝
                      </Button>
                    )}
                    {fact.status !== 'pending' && (
                      <Button variant="secondary" size="sm" disabled={Boolean(busy)} onClick={() => reviewFact(fact, 'pending')}>
                        退回待审核
                      </Button>
                    )}
                    <Button size="sm" disabled={Boolean(busy)} onClick={() => reviewFact(fact, 'accepted')}>
                      <Check className="mr-1 h-3 w-3" />{fact.status === 'accepted' ? '保存修改' : '接受'}
                    </Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle>3. 构建、预览与启用主简历</CardTitle>
              <p className="mt-1 text-xs text-muted">
                先确认事实缺口和个人贡献边界，再按项目归并多个 STAR；生成后在同一区域预览、下载并启用。
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              disabled={Boolean(busy) || acceptedCount === 0}
              onClick={refreshClarifications}
            >
              {busy === 'refresh-clarifications' && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              刷新待确认问题
            </Button>
          </div>
        </CardHeader>
        <CardContent className="grid gap-5 xl:grid-cols-2">
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs text-muted">
              <span>待确认 {openClarifications.length} 项 · 已回答 {answeredClarifications.length} 项</span>
              <span>问题使用中文，并说明对应材料与当前事实</span>
            </div>
            {openClarifications.length === 0 ? (
              <p className="rounded-xl border border-dashed border-card-border p-6 text-center text-sm text-muted">
                {acceptedCount === 0
                  ? '请先接受材料事实，再刷新待确认问题。'
                  : '当前没有待确认问题。已回答内容会在下次生成时融合进正文。'}
              </p>
            ) : openClarifications.map(item => (
              <div key={item.id} className="rounded-xl border border-card-border p-4">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <span className="text-xs font-bold text-primary">
                    {clarificationKindLabels[item.kind] || '补充事实'}
                    {item.priority >= 90 ? ' · 高优先级' : ''}
                  </span>
                  <span className="truncate text-xs text-muted">{item.kind === 'conflict' ? '跨材料逐项核对' : (item.source_filename || '当前材料')}</span>
                </div>
                <p className="text-sm font-medium leading-6">{item.question}</p>
                {item.metadata?.conflict_options?.length ? (
                  <div className="mt-3 rounded-lg bg-[#FFFCFA] p-3 text-xs leading-5 text-muted">
                    <p className="font-bold text-foreground">系统正在逐项比较以下信息</p>
                    <div className="mt-2 space-y-2">
                      {item.metadata.conflict_options.map(option => (
                        <div key={`${option.label}:${option.value}`} className="rounded-lg border border-card-border bg-white p-3">
                          <p className="font-medium text-foreground">
                            <span className="mr-2 rounded bg-primary/10 px-2 py-0.5 text-primary">{option.label}</span>
                            {item.metadata?.field_label || '字段'}：{option.value}
                          </p>
                          {option.details.map(detail => (
                            <p key={detail} className="mt-1 break-words">来源与原文：{detail}</p>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (item.metadata?.context || item.fact_content) && (
                  <div className="mt-3 rounded-lg bg-[#FFFCFA] p-3 text-xs leading-5 text-muted">
                    <p className="font-bold text-foreground">当前已有事实</p>
                    <p className="mt-1">{item.metadata?.context || item.fact_content}</p>
                  </div>
                )}
                {item.metadata?.answer_guidance && (
                  <p className="mt-2 text-xs leading-5 text-muted">
                    <span className="font-bold text-foreground">怎么回答：</span>{item.metadata.answer_guidance}
                  </p>
                )}
                {item.metadata?.resume_effect && (
                  <p className="mt-1 text-xs leading-5 text-primary">
                    <span className="font-bold">回答后：</span>{item.metadata.resume_effect}
                  </p>
                )}
                <textarea
                  className="mt-3 min-h-20 w-full rounded-lg border border-card-border bg-white p-3 text-sm outline-none focus:border-primary"
                  placeholder={item.kind === 'conflict' ? '例如：保留选项 1；或说明选项 1、2 分别属于哪段经历' : '填写可在面试中解释、可追溯的真实信息'}
                  value={clarificationDrafts[item.id] || ''}
                  onChange={event => setClarificationDrafts(current => ({
                    ...current,
                    [item.id]: event.target.value,
                  }))}
                  maxLength={2000}
                />
                <div className="mt-2 flex justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={Boolean(busy)}
                    onClick={() => updateClarification(item, 'dismissed')}
                  >
                    忽略
                  </Button>
                  <Button
                    size="sm"
                    disabled={Boolean(busy) || !(clarificationDrafts[item.id] || '').trim()}
                    onClick={() => updateClarification(item, 'answered')}
                  >
                    <Check className="mr-1 h-3 w-3" />确认回答
                  </Button>
                </div>
              </div>
            ))}
          </div>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <label htmlFor="resume-target-role" className="text-xs font-bold text-muted">
                目标岗位（只影响排序和篇幅）
              </label>
              <Input
                id="resume-target-role"
                value={targetRole}
                onChange={event => setTargetRole(event.target.value)}
                placeholder="如：Linux 内核稳定性 / Android BSP"
                maxLength={100}
              />
              <p className="text-xs text-muted">目标岗位不是事实来源，不会据此补写材料中不存在的技术或成果。</p>
            </div>
            <Button className="w-full" disabled={Boolean(busy) || acceptedCount === 0} onClick={composeProfile}>
              {busy === 'compose-profile' ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
              生成项目化 STAR 主简历
            </Button>
            {!latestProfile ? (
              <p className="rounded-xl border border-dashed border-card-border p-8 text-center text-sm text-muted">
                尚未生成主简历档案。
              </p>
            ) : (
              <>
                <div className="rounded-xl border border-card-border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-black">{latestProfile.name}</p>
                      <p className="mt-1 text-xs text-muted">
                        目标岗位 {latestProfile.quality_report.target_role || '通用'}
                        {' · '}
                        使用 {latestProfile.quality_report.used_fact_count || 0}/{latestProfile.quality_report.accepted_fact_count || 0} 条事实
                        {' · '}证据覆盖率 {Math.round((latestProfile.quality_report.evidence_coverage || 0) * 100)}%
                        {' · '}未解决问题 {latestProfile.quality_report.open_clarification_count || 0}
                        {Boolean(latestProfile.quality_report.validation_warning_count) && (
                          <> · 已剔除 {latestProfile.quality_report.validation_warning_count} 条无来源表达</>
                        )}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <a href={'/api/resume-studio/profile/versions/' + latestProfile.id + '/download'}>
                        <Button variant="secondary" size="sm"><Download className="mr-1 h-3 w-3" />Markdown</Button>
                      </a>
                      <a href={'/api/resume-studio/profile/versions/' + latestProfile.id + '/download?format=json'}>
                        <Button variant="secondary" size="sm">JSON</Button>
                      </a>
                      <Button
                        size="sm"
                        disabled={Boolean(busy) || latestProfile.status === 'active'}
                        onClick={() => activateProfile(latestProfile)}
                      >
                        {latestProfile.status === 'active' ? '当前已启用' : '启用为主简历'}
                      </Button>
                    </div>
                  </div>
                </div>
                <pre className="max-h-[560px] overflow-auto whitespace-pre-wrap rounded-xl border border-card-border bg-[#FFFCFA] p-5 text-sm leading-6">
                  {latestProfile.markdown}
                </pre>
                {Boolean(
                  latestProfile.profile_json?.known_gaps?.length
                  || latestProfile.profile_json?.approved_framings?.length
                ) && (
                  <details className="rounded-xl border border-card-border p-4">
                    <summary className="cursor-pointer text-sm font-bold">
                      审核信息（不会进入预览、下载或启用的简历正文）
                    </summary>
                    {Boolean(latestProfile.profile_json?.known_gaps?.length) && (
                      <div className="mt-3">
                        <p className="text-xs font-bold text-muted">待补充事实</p>
                        <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-muted">
                          {latestProfile.profile_json?.known_gaps?.map((item, index) => (
                            <li key={`gap-${index}`}>{item.text}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {Boolean(latestProfile.profile_json?.approved_framings?.length) && (
                      <div className="mt-3">
                        <p className="text-xs font-bold text-muted">已确认表达边界</p>
                        <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-muted">
                          {latestProfile.profile_json?.approved_framings?.map((item, index) => (
                            <li key={`framing-${index}`}>{item.text}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </details>
                )}
                {workspace.profile_versions.length > 1 && (
                  <div className="border-t border-card-border pt-3">
                    <p className="mb-2 text-xs font-bold text-muted">历史版本</p>
                    {workspace.profile_versions.slice(1).map(profile => (
                      <div key={profile.id} className="flex items-center justify-between py-1 text-xs text-muted">
                        <span>{profile.name}{profile.status === 'active' ? ' · 已启用' : ''}</span>
                        <a className="text-primary hover:underline" href={'/api/resume-studio/profile/versions/' + profile.id + '/download'}>下载</a>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
