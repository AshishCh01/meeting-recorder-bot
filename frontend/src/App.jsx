import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import { SidebarProvider } from './context/SidebarContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import { PublicOnlyRoute } from './components/PublicOnlyRoute';
import { Landing } from './pages/Landing';
import { Login } from './pages/Login';
import { Register } from './pages/Register';
import { Dashboard } from './pages/Dashboard';
import { Upcoming } from './pages/Upcoming';
import { MeetingView } from './pages/MeetingView';
import { Settings } from './pages/Settings';
import { AskAI } from './pages/AskAI';

function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <SidebarProvider>
          <Router>
            <Routes>
              <Route
                path="/"
                element={
                  <PublicOnlyRoute>
                    <Landing />
                  </PublicOnlyRoute>
                }
              />
              <Route
                path="/login"
                element={
                  <PublicOnlyRoute>
                    <Login />
                  </PublicOnlyRoute>
                }
              />
              <Route
                path="/register"
                element={
                  <PublicOnlyRoute>
                    <Register />
                  </PublicOnlyRoute>
                }
              />
              <Route
                path="/dashboard"
                element={
                  <ProtectedRoute>
                    <Dashboard />
                  </ProtectedRoute>
                }
              />
              <Route
                path="/upcoming"
                element={
                  <ProtectedRoute>
                    <Upcoming />
                  </ProtectedRoute>
                }
              />
              <Route
                path="/meetings/:id"
                element={
                  <ProtectedRoute>
                    <MeetingView />
                  </ProtectedRoute>
                }
              />
              {/* /ask opens (or creates) the user's empty chat and moves to its
                  own URL. One route with an optional segment, so that move - and
                  switching between chats - keeps the page, and its chat list,
                  mounted instead of reloading it. */}
              <Route
                path="/ask/:conversationId?"
                element={
                  <ProtectedRoute>
                    <AskAI />
                  </ProtectedRoute>
                }
              />
              <Route
                path="/settings"
                element={
                  <ProtectedRoute>
                    <Settings />
                  </ProtectedRoute>
                }
              />
            </Routes>
          </Router>
        </SidebarProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
