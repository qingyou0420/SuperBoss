import type { UserRole } from '../api/auth'
import { shellCopy } from '../copy/pages/shell'

export interface NavItem {
    to: string
    label: string
}

export function homePath(role: UserRole | undefined): string {
    if (role === 'OWNER') return '/chat'
    if (role === 'MANAGER') return '/overview'
    return '/workbench'
}

export function mainNav(role: UserRole | undefined): NavItem[] {
    const work: NavItem[] = [
        { to: '/projects', label: shellCopy.projects },
        { to: '/drive', label: shellCopy.drive },
        { to: '/knowledge', label: shellCopy.knowledge },
    ]
    const map: NavItem = { to: '/map', label: shellCopy.map }
    const finance: NavItem = { to: '/finance', label: shellCopy.finance }
    const overview: NavItem = { to: '/overview', label: shellCopy.overview }
    const bench: NavItem = { to: '/workbench', label: shellCopy.workbench }
    if (role === 'OWNER')
        return [
            { to: '/chat', label: shellCopy.chat },
            bench,
            map,
            overview,
            finance,
            ...work,
        ]
    if (role === 'MANAGER') return [overview, finance, ...work]
    return [bench, map, ...work]
}

export function accountNav(role: UserRole | undefined): NavItem[] {
    if (role === 'OWNER') {
        return [
            { to: '/members', label: shellCopy.members },
            { to: '/audit', label: shellCopy.audit },
            { to: '/soul', label: shellCopy.soul },
            { to: '/memory', label: shellCopy.memory },
            { to: '/password/change', label: shellCopy.password },
        ]
    }
    return [{ to: '/password/change', label: shellCopy.password }]
}
