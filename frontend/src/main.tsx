import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { api, type Meta } from "./api";
import { MetaContext } from "./components/bits";
import AddMaterial from "./pages/AddMaterial";
import CampaignPage from "./pages/CampaignPage";
import Campaigns from "./pages/Campaigns";
import HowItWorks from "./pages/HowItWorks";
import ItemPage from "./pages/ItemPage";
import Library from "./pages/Library";
import "./styles.css";

/** Open on the Library when there is material, otherwise on Add material. */
function Home() {
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => {
    api
      .library()
      .then((d) => setTarget(d.items.length || d.jobs.length ? "/library" : "/add"))
      .catch(() => setTarget("/library"));
  }, []);
  return target ? <Navigate to={target} replace /> : null;
}

function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => {
    api.meta().then(setMeta).catch(() => undefined);
  }, []);
  return (
    <MetaContext.Provider value={meta}>
      <header className="top">
        <Link className="brand" to="/library">
          Follow the Fake
        </Link>
        <nav>
          <NavLink to="/add">Add material</NavLink>
          <NavLink to="/library">Library</NavLink>
          <NavLink to="/campaigns">Campaigns</NavLink>
          <NavLink to="/how-it-works">How it works</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/add" element={<AddMaterial />} />
          <Route path="/library" element={<Library />} />
          <Route path="/campaigns" element={<Campaigns />} />
          <Route path="/campaigns/kit/:id" element={<CampaignPage kit />} />
          <Route path="/campaigns/:id" element={<CampaignPage />} />
          <Route path="/item/:id" element={<ItemPage />} />
          <Route path="/probe" element={<Navigate to="/add" replace />} />
          <Route path="/how-it-works" element={<HowItWorks />} />
          <Route path="*" element={<Navigate to="/library" replace />} />
        </Routes>
      </main>
    </MetaContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
