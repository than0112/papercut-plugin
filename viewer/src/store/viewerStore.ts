import { create } from "zustand";

/**
 * Viewer-only UI state. It never mutates scene.json: these are live preview
 * adjustments layered on top of the loaded scene. Persistent edits go through
 * the Hermes tool papercraft_update_scene, which rewrites scene.json.
 */
interface ViewerState {
  explode: number; // 0..1
  lightScale: number; // multiplier on every light intensity
  shadows: boolean;
  parallax: boolean;
  preset: string | null; // key of scene.lightingPresets, null = scene.lighting
  hidden: Record<string, boolean>;
  selected: string | null;
  resetToken: number;
  setExplode: (v: number) => void;
  setLightScale: (v: number) => void;
  setShadows: (v: boolean) => void;
  setParallax: (v: boolean) => void;
  setPreset: (v: string | null) => void;
  toggleLayer: (id: string) => void;
  select: (id: string | null) => void;
  resetCamera: () => void;
  resetAll: (defaults: { shadows: boolean; parallax: boolean }) => void;
}

export const useViewer = create<ViewerState>()((set) => ({
  explode: 0,
  lightScale: 1,
  shadows: true,
  parallax: true,
  preset: null,
  hidden: {},
  selected: null,
  resetToken: 0,
  setExplode: (explode) => set({ explode }),
  setLightScale: (lightScale) => set({ lightScale }),
  setShadows: (shadows) => set({ shadows }),
  setParallax: (parallax) => set({ parallax }),
  setPreset: (preset) => set({ preset }),
  toggleLayer: (id) => set((s) => ({ hidden: { ...s.hidden, [id]: !s.hidden[id] } })),
  select: (selected) => set({ selected }),
  resetCamera: () => set((s) => ({ resetToken: s.resetToken + 1 })),
  resetAll: ({ shadows, parallax }) =>
    set((s) => ({
      explode: 0,
      lightScale: 1,
      shadows,
      parallax,
      preset: null,
      hidden: {},
      selected: null,
      resetToken: s.resetToken + 1,
    })),
}));
