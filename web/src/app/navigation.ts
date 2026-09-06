import type { UserRole } from '../api/auth'
import { shellCopy } from '../copy/pages/shell'

export interface NavItem {
    to: string
    label: string
}

export function homePath(role: UserRole | undefined): string {
    return role === 'OWNER' ? '/chat' : '/projects'
}

export function mainNav(role: UserRole | undefined): NavItem[] {
    const shared: NavItem[] = [
        { to: '/finance', label: shellCopy.finance },
        { to: '/projects', label: shellCopy.projects },
        { to: '/drive', label: shellCopy.drive },
        { to: '/knowledge', label: shellCopy.knowledge },
    ]
    if (role === 'OWNER')
        return [{ to: '/chat', label: shellCopy.chat }, ...shared]
    return shared
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
