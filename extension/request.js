async function marketRequest({endpoint, body, account}) {
  if (location.origin !== 'https://www.autodl.com') throw Error('请打开 AutoDL 窗口');
  const token = localStorage.getItem('token');
  const user = JSON.parse(localStorage.getItem('user') || '{}');
  const identity = user.uuid || user.id;
  if (!token || !identity || location.pathname.includes('login'))
    throw Error('请在专用窗口完成 AutoDL 登录');
  if (account && String(identity) !== account) throw Error('账号发生变化，请检查登录账号');
  if (document.querySelector('[id*=captcha]:not(script)')?.getBoundingClientRect().height > 0)
    throw Error('请在专用窗口完成人机验证');
  const prefix = user.sub_name ? '/api/v1/sub_user/' : '/api/v1/';
  const response = await fetch(prefix + endpoint, {
    method: body === null ? 'GET' : 'POST', redirect: 'error',
    signal: AbortSignal.timeout(15000),
    headers: {Authorization: token, 'Content-Type': 'application/json',
      AppVersion: JSON.parse(localStorage.getItem('app_version') || '{}').version || '0.0.0'},
    body: body === null ? undefined : JSON.stringify(body)
  });
  const result = await response.json();
  return {code:result.code, msg:result.msg, data:result.data, account:String(identity)};
}
