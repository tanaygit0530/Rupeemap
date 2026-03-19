import { create } from 'zustand'

interface InvestigationStore {
  officerRole: 'junior' | 'senior' | null
  officerId: string | null
  officerEmail: string | null
  selectedAlertId: string | null
  graphImageBase64: string | null
  sarLoadingStage: 0 | 1 | 2 | 3
  demoScenarioLoaded: string | null

  setOfficer: (id: string, email: string, role: 'junior' | 'senior') => void
  clearOfficer: () => void
  setSelectedAlert: (id: string | null) => void
  setGraphImage: (b64: string | null) => void
  setSarStage: (stage: 0 | 1 | 2 | 3) => void
  setDemoScenario: (scenario: string | null) => void
}

export const useInvestigationStore = create<InvestigationStore>((set) => ({
  officerRole: null,
  officerId: null,
  officerEmail: null,
  selectedAlertId: null,
  graphImageBase64: null,
  sarLoadingStage: 0,
  demoScenarioLoaded: null,

  setOfficer: (id, email, role) => set({ officerId: id, officerEmail: email, officerRole: role }),
  clearOfficer: () => set({ officerRole: null, officerId: null, officerEmail: null }),
  setSelectedAlert: (id) => set({ selectedAlertId: id }),
  setGraphImage: (b64) => set({ graphImageBase64: b64 }),
  setSarStage: (stage) => set({ sarLoadingStage: stage }),
  setDemoScenario: (scenario) => set({ demoScenarioLoaded: scenario }),
}))
