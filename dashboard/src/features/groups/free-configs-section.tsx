/**
 * Free Configs inside the panel's own Create/Edit Group dialog (fork feature).
 *
 * This lives in its own file so the change to upstream's group-modal.tsx stays
 * at an import, a ref and one element - the smaller that diff, the cheaper it
 * is to rebase the fork on a new PasarGuard release.
 *
 * It does its own fetching with plain fetch() rather than a generated hook,
 * because these endpoints are the fork's and are not in upstream's OpenAPI
 * client. They are owner-only, so a sub-admin gets 403 and the section simply
 * renders nothing rather than showing an error for a feature they cannot use.
 *
 * Saving is imperative: on create there is no group id until the group exists,
 * so the modal calls save(id) with the id the create call returned.
 */
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem } from '@/components/ui/command'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { getAuthToken } from '@/utils/authStorage'
import { X } from 'lucide-react'
import { forwardRef, useEffect, useImperativeHandle, useState } from 'react'

export interface FreeConfigsSectionHandle {
  /** Persist the current choice against a group id. Never throws. */
  save: (groupId?: number) => Promise<void>
}

interface FreeConfigsSectionProps {
  open: boolean
  groupId?: number
}

interface PoolConfig {
  uri_hash: string
  uri: string
  protocol: string
  address: string
  port: number
  remark?: string | null
}

async function callApi(path: string, options: { method?: string; body?: string; headers?: Record<string, string> } = {}) {
  const response = await fetch('/api/free-configs' + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer ' + (getAuthToken() || ''),
      ...(options.headers || {}),
    },
  })
  if (!response.ok) throw new Error(String(response.status))
  return response.status === 204 ? null : response.json()
}

/** Behind a CDN every config shares one address; the server name is what differs. */
function sniOf(uri: string): string {
  try {
    if (uri.startsWith('vmess://')) {
      const body = uri.slice(8).replace(/-/g, '+').replace(/_/g, '/')
      const json = JSON.parse(atob(body + '='.repeat((4 - (body.length % 4)) % 4)))
      return json.sni || json.host || ''
    }
    const query = new URLSearchParams((uri.split('?')[1] || '').split('#')[0])
    return query.get('sni') || query.get('host') || query.get('peer') || ''
  } catch {
    return ''
  }
}

const FreeConfigsSection = forwardRef<FreeConfigsSectionHandle, FreeConfigsSectionProps>(function FreeConfigsSection(
  { open, groupId },
  ref,
) {
  const [available, setAvailable] = useState(false)
  const [loading, setLoading] = useState(false)
  const [enabled, setEnabled] = useState(false)
  const [configs, setConfigs] = useState<PoolConfig[]>([])
  const [chosen, setChosen] = useState<string[]>([])

  useEffect(() => {
    if (!open) return
    let cancelled = false

    ;(async () => {
      setLoading(true)
      try {
        const page = await callApi('/configs?limit=500&status=enabled')
        if (cancelled) return
        setConfigs(page.items || [])
        setAvailable(true)

        if (groupId) {
          const state = await callApi(`/groups/${groupId}/state`)
          if (cancelled) return
          setEnabled(!!state.enabled)
          setChosen(state.uri_hashes || [])
        } else {
          setEnabled(false)
          setChosen([])
        }
      } catch {
        // owner-only, or the fork's endpoints are not there: show nothing
        if (!cancelled) setAvailable(false)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [open, groupId])

  useImperativeHandle(ref, () => ({
    async save(id?: number) {
      const target = id ?? groupId
      if (!available || !target) return
      try {
        await callApi(`/groups/${target}/access`, {
          method: 'PUT',
          body: JSON.stringify({ enabled }),
        })
        if (enabled) {
          await callApi(`/groups/${target}/configs`, {
            method: 'PUT',
            body: JSON.stringify({ uri_hashes: chosen }),
          })
        }
      } catch {
        // saving the group itself already succeeded; a failure here must not
        // turn that into an error the user has to interpret
      }
    },
  }))

  if (!available && !loading) return null

  // Belt and braces. The CSS below keeps a long name from stretching the
  // dialog, but these names run to sixty-odd characters and a hard cap means
  // the layout cannot depend on truncation behaving.
  const label = (config: PoolConfig) => {
    const name = config.remark || sniOf(config.uri) || config.address
    const trimmed = name.length > 44 ? name.slice(0, 43) + '…' : name
    return `${config.protocol} · ${trimmed}`
  }

  // contain: inline-size means this box's width comes from its parent and its
  // contents cannot push it wider. That, and w-0 on the name below, are what
  // actually stop a sixty-character hostname from widening the whole dialog -
  // confirmed by rebuilding the dialog's element chain and measuring it, after
  // min-w-0 alone measurably did nothing.
  return (
    <div className="w-full max-w-full min-w-0 space-y-2 overflow-hidden" style={{ contain: 'inline-size' }}>
      <div className="flex items-center justify-between gap-2">
        <label className="shrink-0 text-sm font-medium">Free Configs</label>
        <label className="flex shrink-0 cursor-pointer items-center gap-2 text-xs text-muted-foreground">
          <input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} />
          Give this group free configs
        </label>
      </div>

      {enabled &&
        (loading ? (
          <div className="space-y-2 px-2 py-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="flex items-center gap-2">
                <Skeleton className="h-4 w-4 rounded-sm" />
                <Skeleton className="h-4 w-full max-w-[220px]" />
              </div>
            ))}
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between gap-2">
              <p className="min-w-0 truncate text-xs text-muted-foreground">
                {chosen.length === 0
                  ? 'Nothing picked — this group gets every free config.'
                  : `${chosen.length} picked.`}
              </p>
              {chosen.length > 0 && (
                <Button type="button" variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setChosen([])}>
                  Give it all of them
                </Button>
              )}
            </div>

            <Command className="mb-3 w-full max-w-full min-w-0 overflow-hidden rounded-md border">
              <CommandInput placeholder="Search free configs..." />
              <CommandEmpty>No free configs in the pool yet.</CommandEmpty>
              <CommandGroup dir="ltr" className="max-h-40 w-full max-w-full overflow-auto">
                {configs.map(config => (
                  <CommandItem
                    className="flex items-center gap-2 overflow-hidden"
                    key={config.uri_hash}
                    value={`${config.protocol} ${config.address} ${sniOf(config.uri)} ${config.remark || ''}`}
                    onSelect={() =>
                      setChosen(current =>
                        current.includes(config.uri_hash)
                          ? current.filter(hash => hash !== config.uri_hash)
                          : [...current, config.uri_hash],
                      )
                    }
                  >
                    <div
                      className={cn(
                        'h-4 w-4 shrink-0 rounded-sm border',
                        chosen.includes(config.uri_hash) ? 'border-primary bg-primary' : 'border-muted',
                      )}
                    />
                    <span className="w-0 min-w-0 flex-1 truncate">{label(config)}</span>
                    <span className="max-w-[120px] shrink-0 truncate text-xs text-muted-foreground">
                      {config.address}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </Command>

            <div className="flex w-full min-w-0 flex-wrap gap-2">
              {chosen.map(hash => {
                const config = configs.find(item => item.uri_hash === hash)
                return (
                  <Badge key={hash} variant="secondary" className="flex max-w-full items-center gap-1 overflow-hidden">
                    <span className="w-0 min-w-0 max-w-[180px] flex-1 truncate">{config ? label(config) : hash.slice(0, 8)}</span>
                    <X
                      className="h-3 w-3 cursor-pointer"
                      onClick={() => setChosen(current => current.filter(item => item !== hash))}
                    />
                  </Badge>
                )
              })}
            </div>
          </>
        ))}
    </div>
  )
})

export default FreeConfigsSection
