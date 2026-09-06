export const errorCopy = {
    generic: '操作失败。',
    login: '用户名或密码不正确',
    unauthorized: '登录已失效，请重新登录。',
    forbidden: '没有权限',
    retry: '重试',
    unavailable: '操作失败。',
}

export function shortRequestId(id: string | undefined): string {
    if (!id) return ''
    return id.length > 8 ? id.slice(0, 8) : id
}
