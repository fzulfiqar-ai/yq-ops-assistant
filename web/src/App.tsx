import { lazy } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ProtectedRoute, Gate } from '@/components/guards'
import { useAuth } from '@/lib/auth'
import { homeFor } from '@/lib/nav'
import Login from '@/pages/Login'
import AcceptInvite from '@/pages/AcceptInvite'

// Route-level code-splitting — each page is its own chunk.
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Feed = lazy(() => import('@/pages/Feed'))
const Assistant = lazy(() => import('@/pages/Assistant'))
const FieldNotes = lazy(() => import('@/pages/FieldNotes'))
const Orders = lazy(() => import('@/pages/Orders'))
const OrderDetail = lazy(() => import('@/pages/OrderDetail'))
const Leads = lazy(() => import('@/pages/Leads'))
const Coaching = lazy(() => import('@/pages/Coaching'))
const Agents = lazy(() => import('@/pages/Agents'))
const Catalog = lazy(() => import('@/pages/Catalog'))
const Inventory = lazy(() => import('@/pages/Inventory'))
const StockMovement = lazy(() => import('@/pages/StockMovement'))
const Sales = lazy(() => import('@/pages/Sales'))
const Margins = lazy(() => import('@/pages/Margins'))
const Receivables = lazy(() => import('@/pages/Receivables'))
const DataPage = lazy(() => import('@/pages/DataPage'))
const Team = lazy(() => import('@/pages/Team'))
const Settings = lazy(() => import('@/pages/Settings'))
const PublicCatalog = lazy(() => import('@/pages/PublicCatalog'))

/** Land on the first page this user can see (a salesman goes straight to Catalog). */
function Home() {
  const { me } = useAuth()
  const isAdmin = me?.role === 'admin'
  const canDashboard = isAdmin || (me?.features || []).includes('Dashboard')
  if (!canDashboard) return <Navigate to={homeFor(me)} replace />
  return <Gate feature="Dashboard"><Dashboard /></Gate>
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/invite" element={<AcceptInvite />} />
        <Route path="/c/:token" element={<PublicCatalog />} />

        <Route element={<ProtectedRoute />}>
          <Route index element={<Home />} />
          <Route path="feed" element={<Gate feature="Live Feed"><Feed /></Gate>} />
          <Route path="agents" element={<Gate feature="AI Agents"><Agents /></Gate>} />
          <Route path="assistant" element={<Gate feature="AI Assistant"><Assistant /></Gate>} />
          <Route path="field-notes" element={<Gate feature="AI Assistant"><FieldNotes /></Gate>} />
          <Route path="catalog" element={<Gate feature="Catalog"><Catalog /></Gate>} />
          <Route path="inventory" element={<Gate feature="Inventory"><Inventory /></Gate>} />
          <Route path="stock" element={<Gate feature="Stock Movement"><StockMovement /></Gate>} />
          <Route path="orders" element={<Gate feature="Orders"><Orders /></Gate>} />
          <Route path="orders/:poNo" element={<Gate feature="Orders"><OrderDetail /></Gate>} />
          <Route path="leads" element={<Gate feature="Leads"><Leads /></Gate>} />
          <Route path="coaching" element={<Gate feature="Sales"><Coaching /></Gate>} />
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
