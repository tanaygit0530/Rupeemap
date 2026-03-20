'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import {
  Shield, AlertTriangle, RefreshCw, LogOut, Activity,
  ChevronUp, ChevronDown, Filter, Eye, FileText,
  Clock, TrendingUp, DollarSign, Zap
} from 'lucide-react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  createColumnHelper,
  SortingState,
} from '@tanstack/react-table'
import { supabase, apiFetch } from '@/lib/api'
import { useInvestigationStore } from '@/store/investigationStore'

// ── Types ─────────────────────────────────────────────────────────────────────

type Alert = {
  id: string
  account_id_masked: string
  flag_type: string
  risk_score: number
  suspicious_amount: number
  freeze_status: string
  frozen_amount: number
  ttl_expires_at: string
  triggered_by: string
  created_at: string
  gemini_explanation?: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const FLAG_BADGE: Record<string, string> = {
  SMURFING: 'badge-red',
  ROUNDTRIP: 'badge-red',
  STRUCTURING: 'badge-amber',
  DORMANT: 'badge-blue',
  PROFILE_MISMATCH: 'badge-blue',
  PRODUCT_SWITCHING: 'badge-purple',
}

const FREEZE_BADGE: Record<string, string> = {
  PARTIAL: 'badge-amber',
  FULL: 'badge-red',
  RELEASED: 'badge-green',
  NONE: 'badge-gray',
}

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

  return <span className={`font-mono text-xs ${cls}`}>{rem}</span>
}

function RiskBar({ score }: { score: number }) {
  const cls = score === 100 ? 'gold' : score >= 80 ? 'high' : score >= 50 ? 'medium' : 'low'
  const color = score === 100 ? '#f59e0b' : score >= 80 ? '#ef4444' : score >= 50 ? '#f59e0b' : '#10b981'
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono font-bold text-sm" style={{ color, minWidth: 28 }}>{score}</span>
      <div className="risk-bar flex-1" style={{ minWidth: 64 }}>
        <div className={`risk-bar-fill ${cls}`} style={{ width: `${score}%` }} />
      </div>
    </div>
  )
}

// ── Demo scenarios ─────────────────────────────────────────────────────────────

const DEMO_SCENARIOS = [
  { value: 'DEMO_STRUCTURING', label: '🏦  DEMO_STRUCTURING — Cross-branch structuring' },
  { value: 'DEMO_SMURFING',    label: '🌐  DEMO_SMURFING — Coordinated city smurfing' },
  { value: 'DEMO_ROUNDTRIP',   label: '🔄  DEMO_ROUNDTRIP — Circular round-trip layering' },
  { value: 'DEMO_DORMANT',     label: '💤  DEMO_DORMANT — Dormant account activation' },
  { value: 'DEMO_PROFILE_MISMATCH', label: '👤  DEMO_PROFILE_MISMATCH — Student profile mismatch' },
]

// ── Component ─────────────────────────────────────────────────────────────────

const columnHelper = createColumnHelper<Alert>()

export default function DashboardPage() {
  const router = useRouter()
  const { officerRole, officerEmail, setDemoScenario, clearOfficer } = useInvestigationStore()

  const [alerts, setAlerts] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)
  const [demoLoading, setDemoLoading] = useState(false)
  const [selectedDemo, setSelectedDemo] = useState('')
  const [sorting, setSorting] = useState<SortingState>([{ id: 'risk_score', desc: true }])
  const [flagFilter, setFlagFilter] = useState('')
  const audioRef = useRef<AudioContext | null>(null)

  // Stats
  const totalActive = alerts.filter(a => ['PARTIAL','FULL'].includes(a.freeze_status)).length
  const criticalCount = alerts.filter(a => a.risk_score >= 80).length
  const totalFrozen = alerts.reduce((s, a) => s + (a.frozen_amount || 0), 0)
  const today = alerts.filter(a => new Date(a.created_at) > new Date(Date.now() - 86_400_000)).length

  const fetchAlerts = useCallback(async () => {
    try {
      const data = await apiFetch('/alerts')
      setAlerts(data)
    } catch (e: any) {
      if (e.message?.includes('401')) {
        router.push('/login')
      }
    } finally {
      setLoading(false)
    }
  }, [router])

  useEffect(() => {
    if (!localStorage.getItem('fft_token')) {
      router.push('/login')
      return
    }
    fetchAlerts()

    // Supabase Realtime
    const channel = supabase
      .channel('alerts-live')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'alerts' }, (payload) => {
        const newAlert = payload.new as Alert
        setAlerts(prev => [newAlert, ...prev])
        toast.success(`🚨 New alert — Risk Score ${newAlert.risk_score} — ${newAlert.flag_type}`, {
          duration: 6000,
        })
        // Subtle beep
        try {
          const ctx = new AudioContext()
          const osc = ctx.createOscillator()
          const gain = ctx.createGain()
          osc.connect(gain)
          gain.connect(ctx.destination)
          osc.frequency.value = 880
          gain.gain.value = 0.05
          osc.start()
          osc.stop(ctx.currentTime + 0.12)
        } catch (_) {}
      })
      .subscribe()

    return () => { supabase.removeChannel(channel) }
  }, [fetchAlerts])

  async function loadDemo(scenario: string) {
    if (!scenario) return
    setDemoLoading(true)
    try {
      const data = await apiFetch(`/demo/load/${scenario}`, { method: 'POST' })
      setDemoScenario(scenario)
      toast.success(`✅ ${scenario} loaded — Alert ID ${data.alert_id?.slice(0, 8)}`)
      await fetchAlerts()
    } catch (e: any) {
      toast.error(`Failed to load demo: ${e.message}`)
    } finally {
      setDemoLoading(false)
    }
  }

  function handleLogout() {
    localStorage.removeItem('fft_token')
    clearOfficer()
    router.push('/login')
  }

  const columns = [
    columnHelper.accessor('account_id_masked', {
      header: 'Account ID',
      cell: info => <span className="font-mono text-xs text-slate-400">{info.getValue()}</span>,
    }),
    columnHelper.accessor('flag_type', {
      header: 'Flag Type',
      cell: info => (
        <span className={`badge ${FLAG_BADGE[info.getValue()] || 'badge-gray'}`}>
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.accessor('risk_score', {
      header: 'Risk Score',
      cell: info => <RiskBar score={info.getValue()} />,
    }),
    columnHelper.accessor('suspicious_amount', {
      header: 'Suspicious Amount',
      cell: info => <span className="font-semibold text-white">{formatINR(info.getValue())}</span>,
    }),
    columnHelper.accessor('freeze_status', {
      header: 'Freeze Status',
      cell: info => (
        <span className={`badge ${FREEZE_BADGE[info.getValue()] || 'badge-gray'}`}>
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.accessor('ttl_expires_at', {
      header: 'TTL Remaining',
      cell: info => info.getValue() ? <TTLCountdown expiresAt={info.getValue()} /> : <span className="text-slate-600">—</span>,
    }),
    columnHelper.accessor('triggered_by', {
      header: 'Source',
      cell: info => (
        <span className="badge badge-gray text-xs">{info.getValue()}</span>
      ),
    }),
    columnHelper.display({
      id: 'actions',
      header: 'Actions',
      cell: ({ row }) => (
        <div className="flex gap-2">
          <button
            id={`trace-btn-${row.original.id}`}
            onClick={() => router.push(`/investigation/${row.original.id}`)}
            className="btn-primary py-1.5 px-3 text-xs gap-1"
          >
            <Eye className="w-3 h-3" />
            Trace
          </button>
          <button
            id={`sar-btn-${row.original.id}`}
            onClick={() => router.push(`/investigation/${row.original.id}?sar=1`)}
            className="btn-danger py-1.5 px-3 text-xs gap-1"
          >
            <FileText className="w-3 h-3" />
            SAR
          </button>
        </div>
      ),
    }),
  ]

  const filtered = flagFilter ? alerts.filter(a => a.flag_type === flagFilter) : alerts

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  })

  return (
    <div className="min-h-screen" style={{ background: 'var(--color-bg)' }}>
      {/* Header */}
      <header className="border-b border-white/5 px-6 py-4 flex items-center justify-between sticky top-0 z-50"
        style={{ background: 'rgba(10,10,20,0.95)', backdropFilter: 'blur(16px)' }}>
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-600 to-blue-600 flex items-center justify-center">
            <Shield className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="font-bold text-white text-sm">RupeeMap</h1>
            <p className="text-xs text-slate-500">AML Intelligence Platform</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {officerEmail && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/5 border border-white/8">
              <div className={`w-2 h-2 rounded-full ${officerRole === 'senior' ? 'bg-amber-400' : 'bg-green-400'}`} />
              <span className="text-xs text-slate-300">{officerEmail}</span>
              <span className={`badge text-xs ${officerRole === 'senior' ? 'badge-amber' : 'badge-green'}`}>
                {officerRole}
              </span>
            </div>
          )}
          <button onClick={fetchAlerts} className="btn-ghost py-1.5 px-3 text-xs gap-1">
            <RefreshCw className="w-3 h-3" />
            Refresh
          </button>
          <button onClick={handleLogout} className="btn-ghost py-1.5 px-3 text-xs gap-1 text-red-400 border-red-500/20">
            <LogOut className="w-3 h-3" />
            Logout
          </button>
        </div>
      </header>

      <main className="max-w-[1600px] mx-auto px-6 py-6 space-y-6">

        {/* Section 1 — Demo Scenario Selector */}
        <div className="glass-card p-5 glow-purple">
          <div className="flex items-center gap-2 mb-3">
            <Zap className="w-4 h-4 text-purple-400" />
            <span className="font-semibold text-white text-sm">Demo Scenario Loader</span>
            <span className="badge badge-purple ml-1">HACKATHON CONTROL</span>
          </div>
          <div className="flex gap-3">
            <select
              id="demo-selector"
              value={selectedDemo}
              onChange={e => setSelectedDemo(e.target.value)}
              className="form-select flex-1 text-sm"
            >
              <option value="">Select a demo scenario to load...</option>
              {DEMO_SCENARIOS.map(s => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
            <button
              id="load-demo-btn"
              onClick={() => loadDemo(selectedDemo)}
              disabled={!selectedDemo || demoLoading}
              className="btn-primary whitespace-nowrap"
              style={{ opacity: (!selectedDemo || demoLoading) ? 0.5 : 1 }}
            >
              {demoLoading ? (
                <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Loading...</>
              ) : (
                <><Zap className="w-4 h-4" /> Load Scenario</>
              )}
            </button>
          </div>
        </div>

        {/* Section 2 — Metric Cards */}
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: 'Active Alerts', value: totalActive, icon: Activity, color: '#6c63ff', sub: 'Partial or Full freeze' },
            { label: 'Critical Alerts', value: criticalCount, icon: AlertTriangle, color: '#ef4444', sub: 'Risk score ≥ 80' },
            { label: 'Total Frozen', value: formatINR(totalFrozen), icon: DollarSign, color: '#f59e0b', sub: 'Lien amount' },
            { label: 'Today\'s Alerts', value: today, icon: TrendingUp, color: '#10b981', sub: 'Last 24 hours' },
          ].map(card => (
            <div key={card.label} className="metric-card fade-in-up">
              <div className="flex items-start justify-between mb-3">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center"
                  style={{ background: `${card.color}20`, border: `1px solid ${card.color}30` }}>
                  <card.icon className="w-5 h-5" style={{ color: card.color }} />
                </div>
              </div>
              <div className="text-2xl font-bold text-white mb-1">{card.value}</div>
              <div className="text-sm font-medium text-slate-300">{card.label}</div>
              <div className="text-xs text-slate-500 mt-0.5">{card.sub}</div>
            </div>
          ))}
        </div>

        {/* Section 3 — Table */}
        <div className="glass-card overflow-hidden">
          <div className="px-6 py-4 border-b border-white/5 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-400" />
              <span className="font-semibold text-white">Alert Queue</span>
              <span className="badge badge-gray">{filtered.length} alerts</span>
            </div>
            <div className="flex items-center gap-3">
              <Filter className="w-3.5 h-3.5 text-slate-400" />
              <select
                id="flag-filter"
                value={flagFilter}
                onChange={e => setFlagFilter(e.target.value)}
                className="form-select text-xs py-2 px-3"
                style={{ width: 180 }}
              >
                <option value="">All Flag Types</option>
                <option value="SMURFING">Smurfing</option>
                <option value="STRUCTURING">Structuring</option>
                <option value="ROUNDTRIP">Round-Trip</option>
                <option value="DORMANT">Dormant</option>
                <option value="PROFILE_MISMATCH">Profile Mismatch</option>
                <option value="PRODUCT_SWITCHING">Product Switching</option>
              </select>
            </div>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="w-8 h-8 border-2 border-purple-500/30 border-t-purple-500 rounded-full animate-spin" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-slate-500">
              <Shield className="w-12 h-12 mb-3 opacity-20" />
              <p className="font-medium">No alerts yet</p>
              <p className="text-sm mt-1">Load a demo scenario above to get started</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  {table.getHeaderGroups().map(hg => (
                    <tr key={hg.id}>
                      {hg.headers.map(header => (
                        <th key={header.id} onClick={header.column.getToggleSortingHandler()}>
                          <div className="flex items-center gap-1">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {header.column.getIsSorted() === 'asc' && <ChevronUp className="w-3 h-3" />}
                            {header.column.getIsSorted() === 'desc' && <ChevronDown className="w-3 h-3" />}
                          </div>
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody>
                  {table.getRowModel().rows.map(row => (
                    <tr key={row.id} className="slide-in">
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
