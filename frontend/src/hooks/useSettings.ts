import { api } from '../api'
import { usePersistedResource } from '../lib/persistedResource'
import type { Settings } from '../types'

function isSettings(value: unknown): value is Settings {
  return (
    value !== null &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    Object.values(value).every((setting) => typeof setting === 'string')
  )
}

export function useSettings() {
  const { data: settings, loading, refreshing, error, refresh } = usePersistedResource(
    'settings',
    api.getSettings,
    isSettings,
  )

  return { settings, loading, refreshing, error, refresh }
}
