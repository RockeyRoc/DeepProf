import React from "react";
import { createRoot } from "react-dom/client";
import "@deepprof/design-system/styles.css";
import "./styles.css";
import "./pet.css";
import App from "./App";
import PetApp from "./PetApp";

const isPetSurface = new URLSearchParams(window.location.search).get("surface") === "pet";
if (isPetSurface) {
  document.documentElement.classList.add("pet-surface");
  document.body.classList.add("pet-surface");
}

createRoot(document.getElementById("root")!).render(isPetSurface ? <PetApp /> : (
  <React.StrictMode>
    <App />
  </React.StrictMode>
));
