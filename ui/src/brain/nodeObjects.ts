// three.js meshes for brain nodes. Geometries and materials are cached by key:
// ~4k nodes share a few hundred of each instead of owning their own.
import * as THREE from 'three'
import type { BrainNode } from './model'
import { nodeOpacity, nodeSize } from './visuals'

const geometries = new Map<string, THREE.SphereGeometry>()
const materials = new Map<string, THREE.MeshLambertMaterial>()

function sphere(radius: number): THREE.SphereGeometry {
  const key = radius.toFixed(1)
  let g = geometries.get(key)
  if (!g) {
    g = new THREE.SphereGeometry(Number(key), 10, 8)
    geometries.set(key, g)
  }
  return g
}

function material(color: string, opacity: number, glow: number): THREE.MeshLambertMaterial {
  const key = `${color}|${opacity}|${glow.toFixed(2)}`
  let m = materials.get(key)
  if (!m) {
    m = new THREE.MeshLambertMaterial({
      color,
      transparent: true,
      opacity,
      emissive: color,
      emissiveIntensity: glow,
    })
    materials.set(key, m)
  }
  return m
}

export function radiusOf(n: BrainNode): number {
  return nodeSize(n) * 1.1
}

/** Trust drives the glow: healthy memories bloom, shaky ones stay dim. */
export function buildNodeObject(n: BrainNode, color: string): THREE.Object3D {
  const r = radiusOf(n)
  const glow = Math.round((0.15 + 0.85 * n.health) * 20) / 20
  const mesh = new THREE.Mesh(sphere(r), material(color, nodeOpacity(n), glow))
  if (!n.pinned) return mesh
  const group = new THREE.Group()
  group.add(mesh)
  group.add(new THREE.Mesh(sphere(r * 1.9), material(color, 0.15, 0.4)))
  return group
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

export function withAlpha(color: string, alpha: number): string {
  const hex = color.replace('#', '')
  if (hex.length !== 6) return color
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16))
  return `rgba(${r},${g},${b},${alpha.toFixed(3)})`
}
