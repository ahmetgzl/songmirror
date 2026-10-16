import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'songmirror-feature-guides'
const listeners = new Set<() => void>()
let visible = true
let initialized = false

function snapshot() {
  if (!initialized) {
    initialized = true
    try {
      visible = window.localStorage.getItem(STORAGE_KEY) !== 'hidden'
    } catch {
      // Keep the preference usable in memory when storage is unavailable.
    }
  }
  return visible
}

function notify() {
  listeners.forEach(listener => listener())
}

function onStorage(event: StorageEvent) {
  if (event.key !== STORAGE_KEY && event.key !== null) return
  visible = event.newValue !== 'hidden'
  notify()
}

function subscribe(listener: () => void) {
  if (!listeners.size) window.addEventListener('storage', onStorage)
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (!listeners.size) window.removeEventListener('storage', onStorage)
  }
}

function setVisible(next: boolean) {
  visible = next
  initialized = true
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? 'visible' : 'hidden')
  } catch {
    // The current session still updates if saving the preference fails.
  }
  notify()
}

/** Shared device preference; individual page disclosures retain their state. */
export function useFeatureGuides(): [boolean, (next: boolean) => void] {
  return [useSyncExternalStore(subscribe, snapshot), setVisible]
}
