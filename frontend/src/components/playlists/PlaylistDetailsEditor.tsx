import { t, useTranslation } from '@/i18n'
import { useState } from 'react'

import { errorMessage } from '@/api'
import type { Account, PlaylistDetailsUpdate } from '@/types'

import { Button } from '../ui/Button'
import { SelectField } from '../ui/SelectField'
import { TextField } from '../ui/TextField'

type Visibility = 'keep' | 'private' | 'public'

interface PlaylistDetailsEditorProps {
  account: Account
  name: string
  description: string
  /** Current visibility from the library listing; null when unknown. */
  isPublic: boolean | null
  onCancel: () => void
  onSave: (changes: PlaylistDetailsUpdate) => Promise<void>
}

/** Inline editor for an owned playlist's details. Only the fields this account
 * can change render, and saving sends only the values that actually changed. */
export function PlaylistDetailsEditor({ account, name, description, isPublic, onCancel, onSave }: PlaylistDetailsEditorProps) {
  useTranslation()
  const fields = new Set(account.editable_details ?? [])
  const [draftName, setDraftName] = useState(name)
  const [draftDescription, setDraftDescription] = useState(description)
  const [visibility, setVisibility] = useState<Visibility>(
    isPublic === null ? 'keep' : isPublic ? 'public' : 'private',
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const changes: PlaylistDetailsUpdate = {}
  if (fields.has('name') && draftName.trim() !== name) changes.name = draftName.trim()
  if (fields.has('description') && draftDescription !== description) changes.description = draftDescription
  if (fields.has('public') && visibility !== 'keep' && (visibility === 'public') !== isPublic) {
    changes.public = visibility === 'public'
  }
  const nameMissing = fields.has('name') && !draftName.trim()

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await onSave(changes)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-label={t('Edit details')} className="rounded-control border border-border bg-inset px-3.5 py-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {fields.has('name') ? (
          <TextField
            label={t('Playlist Name')}
            value={draftName}
            maxLength={200}
            error={nameMissing ? t('Enter a playlist name.') : undefined}
            onChange={(event) => setDraftName(event.target.value)}
          />
        ) : null}
        {fields.has('public') ? (
          <SelectField
            label={t('Visibility')}
            value={visibility}
            options={[
              ...(isPublic === null ? [{ value: 'keep', label: t('Keep current visibility') }] : []),
              { value: 'private', label: t('Private') },
              { value: 'public', label: t('Public') },
            ]}
            onChange={(event) => setVisibility(event.target.value as Visibility)}
          />
        ) : null}
        {fields.has('description') ? (
          <div className="sm:col-span-2">
            <TextField
              label={t('Description (optional)')}
              value={draftDescription}
              maxLength={5000}
              onChange={(event) => setDraftDescription(event.target.value)}
            />
          </div>
        ) : null}
      </div>
      {fields.has('name') ? (
        <p className="mt-2 text-xs leading-relaxed text-text-3">
          {t('Syncs match playlists by name unless they have a pairing on the Playlists page. After a rename, the next sync may treat it as a different playlist.')}
        </p>
      ) : null}
      {!fields.has('public') ? (
        <p className="mt-1 text-xs leading-relaxed text-text-3">
          {t('Change its visibility in {{accountName}}.', { accountName: account.name })}
        </p>
      ) : null}
      {error ? <p role="alert" className="mt-2 text-xs text-danger">{error}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          loading={saving}
          disabled={Object.keys(changes).length === 0 || nameMissing}
          onClick={() => void save()}
        >
          {t('Save changes')}
        </Button>
        <Button size="sm" variant="secondary" disabled={saving} onClick={onCancel}>
          {t('Cancel')}
        </Button>
      </div>
    </section>
  )
}
