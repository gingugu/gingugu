// Adds an UnrealBloomPass to the graph's post-processing composer, once.
import { useEffect, type MutableRefObject } from 'react'
import * as THREE from 'three'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import type { ForceGraphMethods } from 'react-force-graph-3d'

export function useBloom(fgRef: MutableRefObject<ForceGraphMethods | undefined>, ready: boolean) {
  useEffect(() => {
    const fg = fgRef.current
    if (!fg || !ready) return
    // An explicit scene background: without it the composer renders a
    // transparent clear, and the bloom pass lifts it to slate gray.
    fg.scene().background = new THREE.Color('#05060a')
    const composer = fg.postProcessingComposer()
    const pass = new UnrealBloomPass(
      new THREE.Vector2(window.innerWidth, window.innerHeight),
      0.65, // strength
      0.35, // radius
      0.22, // threshold: only genuinely bright things bloom
    )
    composer.addPass(pass)
    return () => {
      composer.removePass(pass)
      pass.dispose()
    }
  }, [fgRef, ready])
}
