import React from "react";
import {
  ArrowRight,
  Users,
  Copy,
  Sparkles,
  ChevronLeft,
  Video,
  Camera,
  Link as LinkIcon,
} from "lucide-react";
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../services/api.js';

export default function HeroSection() {
     const [url, setUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const navigate = useNavigate()


  async function handleSubmit(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.createMeeting(url.trim());
      setUrl('');
      navigate('/recordings');
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <section className="relative pt-24 pb-32 overflow-hidden px-5 md:px-16" id="product">
      <div className="absolute inset-0 grid-bg opacity-30 z-0"></div>

      <div className="max-w-[1280px] mx-auto relative z-10 text-center mb-16">
        <h1 className="text-[40px] md:text-[72px] leading-[44px] md:leading-[80px] font-extrabold tracking-tight text-[#0b1c30] mb-6">
          Your meetings,
          <br />
          <span className="text-[#0066FF]">automatically understood.</span>
        </h1>
        <p className="text-lg leading-7 text-[#424656] max-w-2xl mx-auto mb-10">
          Precision intelligence for high-output professionals. We record,
          transcribe, and analyze so you can focus on the conversation, not
          the notes.
        </p>
        <div className="flex flex-col sm:flex-row justify-center gap-4">
          <div className="w-full max-w-2xl mx-auto flex flex-col sm:flex-row items-center gap-2 p-1 bg-[#ffffff] border border-[#c2c6d8] rounded-xl shadow-sm focus-within:border-[#0050cb] focus-within:ring-1 focus-within:ring-[#0050cb] transition-all">
            <div className="flex-grow flex items-center pl-4 gap-3 w-full">
              <LinkIcon size={20} className="text-[#424656] flex-shrink-0" />
              <form className="flex items-center justify-between w-full" onSubmit={handleSubmit}>
                <input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="Paste a Google Meet, Zoom, or Teams link..."
                className="w-full bg-transparent border-none outline-none py-3 text-base text-[#0b1c30] placeholder:text-[#424656]"
              />
            <button type="submit" disabled={submitting} className="w-full cursor-pointer sm:w-auto whitespace-nowrap text-xs tracking-wider font-medium bg-[#0066FF] hover:bg-[#0044CC] text-[#ffffff] px-8 py-4 rounded-lg transition-all duration-200 shadow-md flex items-center justify-center gap-2">
               {submitting ? 'Sending bot...' : 'Record meeting'}
              <ArrowRight size={18} />
            </button>
              </form>
            </div>
          </div>
        </div>
      </div>

      {/* Hero Product Visual */}
      <div className="max-w-5xl mx-auto relative z-10">
        <div className="bg-[#ffffff] border border-[#c2c6d8] rounded-xl shadow-[0_12px_32px_rgba(0,0,0,0.05)] overflow-hidden flex flex-col md:flex-row h-[600px]">
          {/* Sidebar Context */}
          <div className="w-full md:w-1/3 border-r border-[#c2c6d8] bg-[#f8f9ff] flex flex-col">
            <div className="p-6 border-b border-[#c2c6d8]">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-2 h-2 rounded-full bg-[#ba1a1a] animate-pulse"></div>
                <span className="text-xs tracking-wider font-medium text-[#ba1a1a]">
                  Recording 24:38
                </span>
              </div>
              <h3 className="text-2xl font-semibold text-[#0b1c30] mb-2">
                Product Strategy Q3
              </h3>
              <div className="flex items-center gap-2 text-[#424656]">
                <Users size={16} />
                <span className="text-xs tracking-wider font-medium">
                  7 Participants
                </span>
              </div>
            </div>

            <div className="p-6 flex-grow overflow-y-auto">
              <h4 className="text-xs tracking-wider font-medium text-[#424656] mb-4 uppercase">
                AI Summary
              </h4>
              <div className="space-y-4">
                <div className="border-l-4 border-l-[#0066FF] bg-[#eff4ff] p-4 rounded-r-lg group cursor-pointer hover:bg-[#e5eeff] transition-colors relative">
                  <p className="text-sm text-[#0b1c30]">
                    Agreed to accelerate timeline for v2.0 dashboard launch
                    to Q3. Engineering will need additional resources.
                  </p>
                  <div className="absolute -top-10 left-1/2 -translate-x-1/2 bg-[#213145] text-[#eaf1ff] text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap">
                    View Transcript
                  </div>
                </div>
                <div className="border-l-4 border-[#c2c6d8] bg-[#f8f9ff] p-4 rounded-r-lg">
                  <p className="text-sm text-[#424656]">
                    Marketing campaign delayed pending final design
                    approvals.
                  </p>
                </div>
              </div>

              <h4 className="text-xs tracking-wider font-medium text-[#424656] mb-4 mt-8 uppercase">
                Action Items
              </h4>
              <ul className="space-y-3">
                <li className="flex items-start gap-3 group cursor-pointer">
                  <div className="mt-0.5 w-4 h-4 rounded border border-[#c2c6d8] flex-shrink-0 group-hover:border-[#0050cb] transition-colors"></div>
                  <span className="text-sm text-[#0b1c30]">
                    Sarah to finalize Q3 budget proposal by Friday.
                  </span>
                </li>
                <li className="flex items-start gap-3">
                  <div className="mt-0.5 w-4 h-4 rounded border border-[#c2c6d8] flex-shrink-0"></div>
                  <span className="text-sm text-[#0b1c30]">
                    Dev team to review API constraints.
                  </span>
                </li>
              </ul>
            </div>
          </div>

          {/* Main Content Canvas */}
          <div className="w-full md:w-2/3 flex flex-col bg-[#ffffff]">
            <div className="flex-grow p-8 overflow-y-auto relative">
              <div>
                {/* Transcript Item */}
                <div className="flex gap-4 group cursor-pointer mb-6">
                  <div className="w-8 h-8 rounded-full bg-[#dae2fd] text-[#5c647a] flex items-center justify-center text-xs font-medium flex-shrink-0">
                    JD
                  </div>
                  <div>
                    <div className="flex items-baseline gap-2 mb-1">
                      <span className="text-xs font-semibold text-[#0b1c30]">
                        John Doe
                      </span>
                      <span className="text-xs text-[#424656]">
                        10:04 AM
                      </span>
                    </div>
                    <p className="text-base text-[#0b1c30]">
                      I think we need to look at the user adoption metrics
                      before we commit to the new feature set. If we push
                      it too early, we might confuse the base.
                    </p>
                  </div>
                  <div className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity">
                    <button className="text-[#424656] hover:text-[#0050cb]">
                      <Copy size={18} />
                    </button>
                  </div>
                </div>

                {/* Transcript Item */}
                <div className="flex gap-4 group cursor-pointer mb-6">
                  <div className="w-8 h-8 rounded-full bg-[#cc4204] text-[#fff6f4] flex items-center justify-center text-xs font-medium flex-shrink-0">
                    AS
                  </div>
                  <div>
                    <div className="flex items-baseline gap-2 mb-1">
                      <span className="text-xs font-semibold text-[#0b1c30]">
                        Alice Smith
                      </span>
                      <span className="text-xs text-[#424656]">
                        10:05 AM
                      </span>
                    </div>
                    <p className="text-base text-[#0b1c30]">
                      Agreed, but marketing is pushing for a Q3 release to
                      match the conference schedule. Can engineering
                      expedite the core components?
                    </p>
                  </div>
                </div>

                {/* AI Insight Overlay */}
                <div className="mt-12 mb-8 mx-12 bg-[#f8f9ff] border border-[#c2c6d8] rounded-lg p-4 relative shadow-[0_0_20px_0_rgba(0,102,255,0.15)] group cursor-pointer">
                  <div className="absolute -top-3 -left-3 bg-[#0066FF] text-[#ffffff] rounded-full p-1 shadow-sm">
                    <Sparkles size={16} />
                  </div>
                  <p className="text-sm text-[#0b1c30]">
                    <strong>Insight:</strong> Conflict detected between
                    Engineering timeline (Q4) and Marketing requirements
                    (Q3). Resolution required.
                  </p>
                  <div className="absolute -top-10 right-0 z-20 bg-[#213145] text-[#eaf1ff] text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap">
                    Click to assign action
                  </div>
                </div>
              </div>
            </div>

            {/* Ask AI Input */}
            <div className="p-6 border-t border-[#c2c6d8] bg-[#f8f9ff]">
              <div className="relative group">
                <ChevronLeft
                  size={20}
                  className="absolute left-4 top-1/2 -translate-y-1/2 text-[#424656] group-focus-within:text-[#0066FF] transition-colors"
                />
                <input
                  className="w-full bg-[#ffffff] border border-[#c2c6d8] rounded-full py-3 pl-12 pr-4 focus:outline-none focus:border-[#0066FF] focus:ring-1 focus:ring-[#0066FF] transition-all text-base text-[#0b1c30] placeholder:text-[#424656] group-hover:shadow-[0_0_15px_rgba(0,102,255,0.05)]"
                  placeholder="Ask a question about this meeting..."
                  type="text"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Integrations */}
      <div className="mt-20 text-center">
        <p className="text-xs tracking-widest font-medium text-[#424656] uppercase mb-6">
          Works seamlessly with your tools
        </p>
        <div className="flex justify-center items-center gap-8 opacity-60 grayscale hover:grayscale-0 transition-all duration-300">
          <div className="flex items-center gap-2 text-[#0b1c30]">
            <Video size={28} />
            <span className="text-sm font-semibold">Zoom</span>
          </div>
          <div className="flex items-center gap-2 text-[#0b1c30]">
            <Users size={28} />
            <span className="text-sm font-semibold">Teams</span>
          </div>
          <div className="flex items-center gap-2 text-[#0b1c30]">
            <Camera size={28} />
            <span className="text-sm font-semibold">Meet</span>
          </div>
        </div>
      </div>
    </section>
  );
}