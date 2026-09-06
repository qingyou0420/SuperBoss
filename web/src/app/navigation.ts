import type { UserRole } from '../api/auth'

export interface NavItem {
    to: string
    label: string
}

export function homePath(role: UserRole | undefined): string {
    return role === 'OWNER' ? '/chat' : '/projects'
}

export function mainNav(role: UserRole | undefined): NavItem[] {
    const shared: NavItem[] = [
        { to: '/finance', label: '财务' },
        { to: '/projects', label: '项目' },
        { to: '/drive', label: '网盘' },
        { to: '/knowledge', label: '知识库' },
    ]
    if (role === 'OWNER') return [{ to: '/chat', label: '霜月' }, ...shared]
    return shared
}

export function accountNav(role: UserRole | undefined): NavItem[] {
    if (role === 'OWNER') {
        return [
            { to: '/members', label: '成员' },
            { to: '/audit', label: '审计' },
            { to: '/soul', label: '霜月设置' },
            { to: '/memory', label: '记忆' },
            { to: '/password/change', label: '修改密码' },
        ]
    }
    return [{ to: '/password/change', label: '修改密码' }]
}
