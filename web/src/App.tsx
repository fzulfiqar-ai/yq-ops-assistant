import { lazy } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ProtectedRoute, Gate } from '@/components/guards'
import Login from '@/pages/Login'
import AcceptInvite from '@/pages/AcceptInvite'

// Route-level code-splitting — each page is its own chunk.
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Assistant = lazy(() => import('@/pages/Assistant'))
const Agents = lazy(() => import('@/pages/Agents'))
const Inventory = lazy(() => import('@/pages/Inventory'))
const Sales = lazy(() => import('@/pages/Sales'))
const Margins = lazy(() => import('@/pages/Margins'))
const Receivables = lazy(() => import('@/pages/Receivables'))
const DataPage = lazy(() => import('@/pages/DataPage'))
const Team = lazy(() => import('@/pages/Team'))
const Settings = lazy(() => import('@/pages/Settings'))

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/invite" element={<AcceptInvite />} />

        <Route element={<ProtectedRoute />}>
          <Route index element={<Gate feature="Dashboard"><Dashboard /></Gate>} />
          <Route path="agents" element={<Gate feature="AI Agents"><Agents /></Gate>} />
          <Route path="assistant" element={<Gate feature="AI Assistant"><Assistant /></Gate>} />
          <Route path="inventory" element={<Gate feature="Inventory"><Inventory /></Gate>} />
          <Route path="sales" element={<Gate feature="Sales"><Sales /></Gate>} />
          <Route path="margins" element={<Gate feature="Margins"><Margins /></Gate>} />
          <Route path="receivables" element={<Gate feature="Receivables"><Receivables /></Gate>} />
          <Route path="data" element={<Gate><DataPage /></Gate>} />
          <Route path="team" element={<Gate><Team /></Gate>} />
          <Route path="settings" element={<Settings />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
