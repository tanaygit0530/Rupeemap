'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'
import ReactFlow, {
  Node, Edge, Controls, MiniMap, Background, BackgroundVariant, useNodesState, useEdgesState,
} from 'reactflow'
import 'reactflow/dist/style.css'
import html2canvas from 'html2canvas'
import {
  ArrowLeft, Camera, CheckCircle, FileText, Shield, AlertTriangle,
  Lock, Unlock, TrendingUp, Clock, ChevronRight, Loader2, Info
} from 'lucide-react'
import { useInvestigationStore } from '@/store/investigationStore'
import { apiFetch } from '@/lib/api'

// ── Types ─────────────────────────────────────────────────────────────────────

type AlertDetail = {
  id: string
  account_id_masked: string
  flag_type: string
  risk_score: number
  suspicious_amount: number
  frozen_amount: number
  available_balance?: number
  freeze_status: string
  ttl_expires_at: string
  triggered_by: string
  gemini_explanation: string
  product_chain?: string
  engine1_score: number
  engine2_score: number
  ml_addition: number
  subgraph_data?: {
    accounts: string[]
    branches: Record<string, string>
    channels: Record<string, string>
    cycle_detected: boolean
    cycle_velocity: string
    alert_level: string
    branches_involved: string[]
    cities_involved: string[]
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatINR(n: number) {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n)
}

function TTLCountdown({ expiresAt }: { expiresAt: string }) {
  const [rem, setRem] = useState('')
  const [cls, setCls] = useState('ttl-ok')
  useEffect(() => {
    function tick() {
      const diff = new Date(expiresAt).getTime() - Date.now()
      if (diff <= 0) { setRem('EXPIRED'); setCls('ttl-critical'); return }
      const h = Math.floor(diff / 3_600_000)
      const m = Math.floor((diff % 3_600_000) / 60_000)
      const s = Math.floor((diff % 60_000) / 1_000)
      setRem(`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`)
      setCls(diff < 3_600_000 ? 'ttl-critical' : diff < 14_400_000 ? 'ttl-warning' : 'ttl-ok')
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [expiresAt])
  return <span className={`font-mono font-bold text-lg ${cls}`}>{rem}</span>
}

const SAR_STAGES = [
  'Collecting transaction evidence...',
  'AI writing narrative...',
  'Building PDF...',
]

// ── React Flow node types & builder ─────────────────────────────────────────

type NodeType = 'mule' | 'aggregator' | 'neutral'

const nodeStyle = (type: NodeType) => ({
  background: type === 'mule' ? 'rgba(239,68,68,0.15)' :
    type === 'aggregator' ? 'rgba(245,158,11,0.2)' : 'rgba(255,255,255,0.05)',
  border: `2px solid ${type === 'mule' ? '#ef4444' : type === 'aggregator' ? '#f59e0b' : 'rgba(255,255,255,0.2)'}`,
  borderRadius: '50%',
  width: 90, height: 90,
  display: 'flex', flexDirection: 'column' as const, alignItems: 'center', justifyContent: 'center',
  fontSize: 10, color: '#e2e8f0', textAlign: 'center' as const, padding: 8,
})

function buildGraphElements(alert: AlertDetail): { nodes: Node[], edges: Edge[] } {
  const accounts = alert.subgraph_data?.accounts || [alert.account_id_masked]
  const branches = alert.subgraph_data?.branches || {}

  const nodes: Node[] = accounts.slice(0, 12).map((acc, i) => {
    const isAgg = i === 0 || acc === alert.account_id_masked
    const isMule = !isAgg && alert.flag_type === 'SMURFING'
    const type: NodeType = isAgg ? 'aggregator' : isMule ? 'mule' : 'neutral'
    const angle = (i / accounts.length) * 2 * Math.PI
    const radius = accounts.length > 1 ? 200 : 0
    return {
      id: acc,
      position: {
        x: 300 + radius * Math.cos(angle),
        y: 250 + radius * Math.sin(angle),
      },
      data: {
        label: (
          <div style={nodeStyle(type)}>
            <div style={{ fontWeight: 700, fontSize: 9 }}>{acc.slice(0, 10)}</div>
            <div style={{ fontSize: 8, opacity: 0.7, marginTop: 2 }}>{branches[acc]?.split('/')[1] || 'Unknown'}</div>
            <div style={{ fontSize: 8, opacity: 0.5 }}>{alert.flag_type}</div>
          </div>
        ),
      },
      style: { padding: 0, border: 'none', background: 'transparent' },
    }
  })

  const edges: Edge[] = []
  if (alert.flag_type === 'SMURFING') {
    accounts.slice(1).forEach((acc, i) => {
      edges.push({
        id: `e${i}`,
        source: acc,
        target: accounts[0],
        label: `₹49,000 BRANCH`,
        labelStyle: { fill: '#94a3b8', fontSize: 9 },
        style: { stroke: '#ef4444', strokeWidth: 2 },
        animated: true,
      })
    })
  } else if (alert.flag_type === 'ROUNDTRIP' && accounts.length >= 3) {
    for (let i = 0; i < accounts.length; i++) {
      edges.push({
        id: `e${i}`,
        source: accounts[i],
        target: accounts[(i + 1) % accounts.length],
        label: 'NEFT',
        labelStyle: { fill: '#94a3b8', fontSize: 9 },
        style: { stroke: '#f59e0b', strokeWidth: 2 },
        animated: true,
      })
    }
  } else {
    accounts.slice(1).forEach((acc, i) => {
      edges.push({
        id: `e${i}`,
        source: acc,
        target: accounts[0],
        style: { stroke: '#6c63ff', strokeWidth: 1.5 },
        animated: true,
      })
    })
  }

  return { nodes, edges }
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function InvestigationPage({ params: paramsPromise }: { params: Promise<{ alertId: string }> }) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { officerRole, graphImageBase64, setGraphImage, setSarStage, sarLoadingStage } = useInvestigationStore()

  const [alertId, setAlertId] = useState<string>('')
  const [alert, setAlert] = useState<AlertDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [nodes, setNodes] = useNodesState([])
  const [edges, setEdges] = useEdgesState([])
  const [graphCaptured, setGraphCaptured] = useState(false)
  const [sarLoading, setSarLoading] = useState(false)
  const [confirmRelease, setConfirmRelease] = useState(false)
  const rfRef = useRef<HTMLDivElement>(null)
  const heartbeatRef = useRef<NodeJS.Timeout | null>(null)

  // Unwrap params (Next.js 15 async params)
  useEffect(() => {
    paramsPromise.then(p => setAlertId(p.alertId))
  }, [paramsPromise])

  // Fetch alert
  useEffect(() => {
    if (!alertId) return
    if (!localStorage.getItem('fft_token')) { router.push('/login'); return }

    async function load() {
      try {
        const data = await apiFetch(`/alerts/${alertId}`)
        setAlert(data)
        const { nodes: n, edges: e } = buildGraphElements(data)
        setNodes(n)
        setEdges(e)
      } catch { router.push('/dashboard') }
      finally { setLoading(false) }
    }
    load()

    // Lock + heartbeat
    apiFetch(`/alerts/${alertId}/lock`, { method: 'PATCH' }).catch(() => {})
    heartbeatRef.current = setInterval(() => {
      apiFetch(`/alerts/${alertId}/heartbeat`, { method: 'PATCH' }).catch(() => {})
    }, 3 * 60 * 1000)

    return () => {
      if (heartbeatRef.current) clearInterval(heartbeatRef.current)
    }
  }, [alertId, router])

  // Auto-open SAR flow if redirected from dashboard SAR button
  useEffect(() => {
    if (searchParams.get('sar') && graphCaptured) handleGenerateSAR()
  }, [graphCaptured])

  async function captureGraph() {
    if (!rfRef.current) return
    try {
      const canvas = await html2canvas(rfRef.current, { backgroundColor: '#0a0a14' })
      const b64 = canvas.toDataURL('image/png').split(',')[1]
      setGraphImage(b64)
      setGraphCaptured(true)
      toast.success('✅ Graph captured — ready for SAR')
    } catch {
      toast.error('Graph capture failed — try again')
    }
  }

  async function handleAction(endpoint: string, method = 'POST') {
    try {
      await apiFetch(`/alerts/${alertId}/${endpoint}`, { method })
      toast.success('Action completed')
      const data = await apiFetch(`/alerts/${alertId}`)
      setAlert(data)
    } catch (e: any) {
      toast.error(e.message)
    }
  }

  async function handleGenerateSAR() {
    if (!graphImageBase64) {
      toast.error('Please capture the graph first')
      return
    }
    setSarLoading(true)
    try {
      // Stage progression
      setSarStage(1)
      await new Promise(r => setTimeout(r, 2000))
      setSarStage(2)
      await new Promise(r => setTimeout(r, 4000))
      setSarStage(3)

      const data = await apiFetch('/sar/generate', {
        method: 'POST',
        body: JSON.stringify({ alert_id: alertId, graph_image: graphImageBase64 }),
      })

      // Trigger download
      const a = document.createElement('a')
      a.href = data.download_url
      a.download = data.filename
      a.click()

      toast.success('✅ SAR PDF downloaded — ready to submit to FIU-IND')
    } catch (e: any) {
      toast.error(`SAR generation failed: ${e.message}`)
    } finally {
      setSarLoading(false)
      setSarStage(0)
    }
  }

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center">
      <Loader2 className="w-10 h-10 text-purple-400 animate-spin" />
    </div>
  )

  if (!alert) return null

  const e1 = alert.engine1_score || 0
  const e2 = alert.engine2_score || 0
  const ml = alert.ml_addition || 0
  const sarProgress = sarLoadingStage === 1 ? 33 : sarLoadingStage === 2 ? 66 : sarLoadingStage === 3 ? 95 : 0

  return (
    <div className="min-h-screen" style={{ background: 'var(--color-bg)' }}>
      {/* Header */}
      <header className="border-b border-white/5 px-6 py-4 flex items-center gap-4 sticky top-0 z-40"
        style={{ background: 'rgba(10,10,20,0.95)', backdropFilter: 'blur(16px)' }}>
        <button onClick={() => router.push('/dashboard')} className="btn-ghost py-1.5 px-3 text-xs gap-1">
          <ArrowLeft className="w-3.5 h-3.5" />
          Dashboard
        </button>
        <ChevronRight className="w-4 h-4 text-slate-600" />
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-purple-400" />
          <span className="font-semibold text-white text-sm">Investigation</span>
          <span className="font-mono text-xs text-slate-500">{alertId.slice(0, 12)}</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className={`badge ${alert.flag_type === 'SMURFING' || alert.flag_type === 'ROUNDTRIP' ? 'badge-red' :
            alert.flag_type === 'STRUCTURING' ? 'badge-amber' : 'badge-blue'}`}>
            {alert.flag_type}
          </span>
          <span className="font-mono font-bold text-white">Risk: {alert.risk_score}/100</span>
        </div>
      </header>

      <div className="max-w-[1600px] mx-auto px-6 py-6">
        <div className="grid grid-cols-2 gap-6">

          {/* ── Left Column — React Flow Graph ─────────────────────────── */}
          <div className="space-y-4 fade-in-up">
            <div className="glass-card overflow-hidden">
              <div className="px-5 py-4 border-b border-white/5 flex items-center justify-between">
                <h2 className="font-semibold text-white flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-purple-400" />
                  Transaction Network Graph
                </h2>
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-400 inline-block" />Mule</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" />Aggregator</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-slate-400 inline-block" />Neutral</span>
                </div>
              </div>
              <div ref={rfRef} id="react-flow-container" style={{ height: 450, background: '#070712' }}>
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  fitView
                  attributionPosition="bottom-right"
                >
                  <Background variant={BackgroundVariant.Dots} color="#1a1a2e" gap={20} />
                  <Controls />
                  <MiniMap
                    style={{ background: '#111128' }}
                    maskColor="rgba(0,0,0,0.4)"
                  />
                </ReactFlow>
              </div>
              <div className="px-5 py-4 border-t border-white/5 flex items-center gap-3">
                <button
                  id="capture-graph-btn"
                  onClick={captureGraph}
                  className={graphCaptured ? 'btn-success py-2 px-4 text-sm' : 'btn-primary py-2 px-4 text-sm'}
                >
                  {graphCaptured ? (
                    <><CheckCircle className="w-4 h-4" /> Graph Captured</>
                  ) : (
                    <><Camera className="w-4 h-4" /> Capture Graph for SAR</>
                  )}
                </button>
                {graphCaptured && (
                  <span className="text-xs text-green-400 flex items-center gap-1">
                    <CheckCircle className="w-3 h-3" /> Ready for SAR generation
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* ── Right Column ────────────────────────────────────────────── */}
          <div className="space-y-4 fade-in-up" style={{ animationDelay: '0.1s' }}>

            {/* AI Explanation */}
            <div className="glass-card p-5">
              <h3 className="font-semibold text-white flex items-center gap-2 mb-3">
                <Info className="w-4 h-4 text-blue-400" />
                AI Explanation
                <span className="badge badge-blue text-xs">Gemini 2.0 Flash</span>
              </h3>
              <div className="ai-explanation p-4 rounded-r-lg">
                <p className="text-sm text-slate-300 leading-relaxed">
                  {alert.gemini_explanation || 'AI explanation will appear here after analysis.'}
                </p>
              </div>
            </div>

            {/* Risk Score Breakdown */}
            <div className="glass-card p-5">
              <h3 className="font-semibold text-white mb-4 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-400" />
                Risk Score Breakdown
              </h3>
              <div className="space-y-3">
                {[
                  { label: 'Engine 1 — Structuring', score: e1, max: 40, color: '#6c63ff', sub: alert.subgraph_data?.alert_level || 'LEVEL_1' },
                  { label: 'Engine 2 — Graph Analysis', score: e2, max: 60, color: '#38bdf8', sub: alert.flag_type },
                  { label: 'Gemini AI Addition', score: ml, max: 20, color: '#10b981', sub: '+bonus points' },
                ].map(row => (
                  <div key={row.label}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-slate-400">{row.label}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-500">{row.sub}</span>
                        <span className="font-mono text-xs font-bold" style={{ color: row.color }}>
                          {row.score}/{row.max}
                        </span>
                      </div>
                    </div>
                    <div className="risk-bar">
                      <div className="risk-bar-fill" style={{
                        width: `${(row.score / row.max) * 100}%`,
                        background: `linear-gradient(90deg, ${row.color}80, ${row.color})`,
                      }} />
                    </div>
                  </div>
                ))}
                <div className="pt-3 border-t border-white/5 flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-300">Final Risk Score</span>
                  <span className="text-2xl font-bold" style={{
                    color: alert.risk_score >= 80 ? '#ef4444' : alert.risk_score >= 50 ? '#f59e0b' : '#10b981'
                  }}>
                    {alert.risk_score}<span className="text-sm text-slate-500 font-normal">/100</span>
                  </span>
                </div>
              </div>
            </div>

            {/* Freeze Panel */}
            <div className="glass-card p-5 glow-red" style={{ border: '1px solid rgba(239,68,68,0.2)' }}>
              <h3 className="font-semibold text-white mb-4 flex items-center gap-2">
                <Lock className="w-4 h-4 text-red-400" />
                Freeze Panel
              </h3>
              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <p className="text-xs text-slate-500 mb-1">Amount Frozen</p>
                  <p className="text-xl font-bold text-red-400">{formatINR(alert.frozen_amount)}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Taint-traced lien</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">TTL Remaining</p>
                  {alert.ttl_expires_at ? <TTLCountdown expiresAt={alert.ttl_expires_at} /> : <span className="text-slate-600">—</span>}
                </div>
              </div>

              <div className="flex gap-2 flex-wrap mb-3">
                {/* Escalate — senior only */}
                {officerRole === 'senior' && (
                  <button
                    id="escalate-btn"
                    onClick={() => handleAction('escalate')}
                    className="btn-danger py-2 px-4 text-sm flex-1"
                  >
                    <Lock className="w-4 h-4" />
                    Escalate to Full Freeze
                  </button>
                )}

                <button
                  id="confirm-btn"
                  onClick={() => handleAction('confirm')}
                  className="btn-warning py-2 px-4 text-sm flex-1"
                >
                  <CheckCircle className="w-4 h-4" />
                  Confirm Partial Freeze
                </button>

                {!confirmRelease ? (
                  <button
                    id="release-btn"
                    onClick={() => setConfirmRelease(true)}
                    className="btn-success py-2 px-4 text-sm flex-1"
                  >
                    <Unlock className="w-4 h-4" />
                    Release — False Alarm
                  </button>
                ) : (
                  <div className="flex-1 space-y-2">
                    <p className="text-xs text-amber-400">
                      ⚠️ Are you sure? This will restore {formatINR(alert.frozen_amount)} to the account and log as false positive.
                    </p>
                    <div className="flex gap-2">
                      <button className="btn-success py-1.5 px-3 text-xs flex-1"
                        onClick={() => { handleAction('release'); setConfirmRelease(false) }}>
                        Yes, Release
                      </button>
                      <button className="btn-ghost py-1.5 px-3 text-xs flex-1"
                        onClick={() => setConfirmRelease(false)}>
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <div className="flex items-center gap-1 text-xs text-slate-500">
                <Clock className="w-3 h-3" />
                Freeze Status: <span className="text-amber-400 font-medium ml-1">{alert.freeze_status}</span>
              </div>
            </div>
          </div>
        </div>

        {/* ── SAR Button — full width ──────────────────────────────────── */}
        <div className="mt-6 glass-card p-5">
          <button
            id="generate-sar-btn"
            onClick={handleGenerateSAR}
            disabled={sarLoading}
            className="w-full btn-primary justify-center py-4 text-base"
            style={{ opacity: sarLoading ? 0.8 : 1, fontSize: 15 }}
          >
            {sarLoading ? (
              <><Loader2 className="w-5 h-5 animate-spin" /> {SAR_STAGES[(sarLoadingStage || 1) - 1]}</>
            ) : (
              <><FileText className="w-5 h-5" /> Generate SAR Report — Submit to FIU-IND</>
            )}
          </button>

          {sarLoading && (
            <div className="mt-3">
              <div className="sar-progress-bar">
                <div className="sar-progress-fill" style={{ width: `${sarProgress}%` }} />
              </div>
              <div className="flex justify-between text-xs text-slate-500 mt-1">
                <span>Stage {sarLoadingStage}/3</span>
                <span>{SAR_STAGES[(sarLoadingStage || 1) - 1]}</span>
              </div>
            </div>
          )}

          {!graphImageBase64 && !sarLoading && (
            <p className="text-xs text-slate-500 text-center mt-3">
              ⬆ Capture the graph above first to enable SAR generation
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
