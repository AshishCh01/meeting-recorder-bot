import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

// Wraps routes that only make sense for a signed-out visitor (landing,
// login, signup) - a signed-in user gets bounced straight to the dashboard.
export const PublicOnlyRoute = ({ children }) => {
  const { session } = useAuth();

  if (session) {
    return <Navigate to="/dashboard" replace />;
  }

  return children;
};
