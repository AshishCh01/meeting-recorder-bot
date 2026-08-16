import Dashboard from './pages/Dashboard.jsx';
import HeroSection from './section/HeroSection.jsx';
import {Recordings} from './pages/Recordings.jsx';
import './index.css'
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';

export default function App() {
  return (
    <Router>
      <Routes>
        <Route index element={<HeroSection />} />
        <Route path='/dashboard' element={<Dashboard />} />
        <Route path='/recordings' element={<Recordings />} />
      </Routes>
    </Router>
  );
}