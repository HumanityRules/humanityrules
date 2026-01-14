import { Routes, Route, NavLink } from 'react-router-dom'
import Dashboard from './components/Dashboard'
import UserList from './components/UserList'
import UserDetail from './components/UserDetail'

function App() {
  return (
    <div className="app">
      <nav className="sidebar">
        <div className="logo">
          <h1>Admin</h1>
        </div>
        <ul className="nav-links">
          <li>
            <NavLink to="/" className={({ isActive }) => isActive ? 'active' : ''}>
              Dashboard
            </NavLink>
          </li>
          <li>
            <NavLink to="/users" className={({ isActive }) => isActive ? 'active' : ''}>
              Users
            </NavLink>
          </li>
        </ul>
      </nav>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/users" element={<UserList />} />
          <Route path="/users/:id" element={<UserDetail />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
