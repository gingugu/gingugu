// Adds an UnrealBloomPass to the graph's post-processing composer, once.
import { useEffect, type MutableRefObject } from 'react'
import * as THREE from 'three'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import type { ForceGraphMethods } from 'react-force-graph-3d'

export function useBloom(fgRef: MutableRefObject<ForceGraphMethods | undefined>, ready: boolean) {
  useEffect(() => {
    const fg = fgRef.current
    if (!fg || !ready) return
    const composer = fg.postProcessingComposer()
    const pass = new UnrealBloomPass(
      new THREE.Vector2(window.innerWidth, window.innerHeight),
      1.1,
      0.6,
      0.15,
    )
    composer.addPass(pass)
    return () => {
      composer.removePass(pass)
      pass.dispose()
    }
  }, [fgRef, ready])
}
