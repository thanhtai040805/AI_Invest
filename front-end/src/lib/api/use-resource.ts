"use client"
import { useCallback, useEffect, useRef, useState } from "react"
import type { ApiProblem } from "./client"
export function useResource<T>(loader: () => Promise<T>, deps: readonly unknown[] = []) {
  const [data, setData] = useState<T | null>(null); const [error, setError] = useState<ApiProblem | null>(null); const [loading, setLoading] = useState(true)
  const loaderRef = useRef(loader)
  const dependencyKey = JSON.stringify(deps)
  const reload = useCallback(async () => { setLoading(true); setError(null); try { setData(await loaderRef.current()) } catch (value) { setError(value as ApiProblem) } finally { setLoading(false) } }, [])
  useEffect(() => { loaderRef.current = loader }, [loader])
  // Resource loading is the external synchronization performed by this hook.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void reload() }, [reload, dependencyKey])
  return { data, error, loading, reload }
}
