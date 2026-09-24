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
const Upcoming = lazy(() => import('@/pages/Upcoming'))
const Finds = lazy(() => import('@/pages/Finds'))
const Inventory = lazy(() => import('@/pages/Inventory'))
const StockMovement = lazy(() => import('@/pages/StockMovement'))
const Sales = lazy(() => import('@/pages/Sales'))
const Margins = lazy(() => import('@/pages/Margins'))
const PriceTracker = lazy(() => import('@/pages/PriceTracker'))
const Receivables = lazy(() => import('@/pages/Receivables'))
const DataPage = lazy(() => import('@/pages/DataPage'))
const Team = lazy(() => import('@/pages/Team'))
const Settings = lazy(() => import('@/pages/Settings'))
const PublicCatalog = lazy(() => import('@/pages/PublicCatalog'))
const PublicFinds = lazy(() => import('@/pages/PublicFinds'))
const Marketing = lazy(() => import('@/pages/Marketing'))
const OptOut = lazy(() => import('@/pages/OptOut'))
const OrderStatus = lazy(() => import('@/pages/OrderStatus'))
const ShopOrders = lazy(() => import('@/pages/ShopOrders'))
const Salesmen = lazy(() => import('@/pages/Salesmen'))
const ShopRules = lazy(() => import('@/pages/ShopRules'))
const ShopAnalytics = lazy(() => import('@/pages/ShopAnalytics'))
// The customer-facing shop, reused inside the portal so a salesman can order for a shop.
const ShopPage = lazy(() => import('@/pages/shop/ShopPage'))
const PickList = lazy(() => import('@/pages/PickList'))
// The salesman app (SalesmanShell): Today · Catalog · Orders · Customers · Me.
const SalesToday = lazy(() => import('@/pages/sales/Today'))
const SalesCustomers = lazy(() => import('@/pages/sales/Customers'))
const SalesAccount = lazy(() => import('@/pages/sales/Account'))

/** Land on the first page this user can see (a salesman goes straight to Catalog). */
function Home() {
  const { me } = useAuth()
  const isAdmin = me?.role === 'admin'
  const canDashboard = isAdmin || (me?.features || []).includes('Dashboard')
  // A salesman never wants the office dashboard — send them to the catalog they sell from.
  if (me?.role === 'salesman') return <Navigate to={homeFor(me)} replace />
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
        <Route path="/f/:token" element={<PublicFinds />} />
        <Route path="/optout/:token" element={<OptOut />} />
        <Route path="/o/:orderToken" element={<OrderStatus />} />

        <Route element={<ProtectedRoute />}>
          <Route index element={<Home />} />
          <Route path="feed" element={<Gate feature="Live Feed"><Feed /></Gate>} />
          <Route path="agents" element={<Gate feature="AI Agents"><Agents /></Gate>} />
          <Route path="assistant" element={<Gate feature="AI Assistant"><Assistant /></Gate>} />
          <Route path="field-notes" element={<Gate feature="AI Assistant"><FieldNotes /></Gate>} />
          <Route path="catalog" element={<Gate feature="Catalog"><Catalog /></Gate>} />
          <Route path="upcoming" element={<Gate feature="Catalog"><Upcoming /></Gate>} />
          <Route path="finds" element={<Gate feature="Product Finds"><Finds /></Gate>} />
          <Route path="inventory" element={<Gate feature="Inventory"><Inventory /></Gate>} />
          <Route path="stock" element={<Gate feature="Stock Movement"><StockMovement /></Gate>} />
          <Route path="orders" element={<Gate feature="Orders"><Orders /></Gate>} />
          <Route path="orders/:poNo" element={<Gate feature="Orders"><OrderDetail /></Gate>} />
          <Route path="leads" element={<Gate feature="Leads"><Leads /></Gate>} />
          <Route path="marketing" element={<Gate feature="Marketing"><Marketing /></Gate>} />
          <Route path="shop" element={<Gate feature="Catalog"><ShopPage mode="salesman" /></Gate>} />
          <Route path="shop-orders" element={<Gate feature="Shop Orders"><ShopOrders /></Gate>} />
          <Route path="today" element={<Gate feature="Shop Orders"><SalesToday /></Gate>} />
          <Route path="customers" element={<Gate feature="Shop Orders"><SalesCustomers /></Gate>} />
          <Route path="account" element={<SalesAccount />} />
          <Route path="picklist" element={<Gate feature="Storekeeper"><PickList /></Gate>} />
          <Route path="salesmen" element={<Gate feature="Shop Admin"><Salesmen /></Gate>} />
          <Route path="shop-rules" element={<Gate feature="Shop Admin"><ShopRules /></Gate>} />
          <Route path="shop-analytics" element={<Gate feature="Shop Admin"><ShopAnalytics /></Gate>} />
          <Route path="coaching" element={<Gate feature="Sales"><Coaching /></Gate>} />
          <Route path="sales" element={<Gate feature="Sales"><Sales /></Gate>} />
          <Route path="margins" element={<Gate feature="Margins"><Margins /></Gate>} />
          <Route path="prices" element={<Gate feature="Margins"><PriceTracker /></Gate>} />
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
