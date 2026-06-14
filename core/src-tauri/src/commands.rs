//! Tauri 命令 - 前端通过 invoke() 调用

use crate::AppState;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::State;

#[derive(Deserialize, Serialize)]
pub struct CoreCallArgs {
    pub method: String,
    pub path: String,
    pub body: Option<Value>,
}

#[tauri::command]
pub async fn get_backend_port(state: State<'_, AppState>) -> Result<u16, String> {
    let p = state.py_port.lock().await;
    p.ok_or_else(|| "Python 后端尚未就绪".to_string())
}

#[tauri::command]
pub async fn core_call(
    args: CoreCallArgs,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let port = {
        let p = state.py_port.lock().await;
        p.ok_or_else(|| "Python 后端未就绪".to_string())?
    };
    
    let url = format!("http://127.0.0.1:{}{}", port, args.path);
    
    // no_proxy:防止把 localhost 请求送给系统代理(用户可能设了 http_proxy)
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| format!("HTTP client 构建失败: {}", e))?;
    
    let req = match args.method.to_uppercase().as_str() {
        "GET" => client.get(&url),
        "POST" => client.post(&url),
        "PUT" => client.put(&url),
        "PATCH" => client.patch(&url),
        "DELETE" => client.delete(&url),
        m => return Err(format!("不支持的方法: {}", m)),
    };
    
    let req = if let Some(body) = args.body {
        req.json(&body)
    } else {
        req
    };
    
    let resp = req.send().await.map_err(|e| format!("请求失败: {}", e))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| format!("读响应失败: {}", e))?;
    
    if !status.is_success() {
        if let Ok(json) = serde_json::from_str::<Value>(&text) {
            return Err(serde_json::to_string(&json).unwrap_or(text));
        }
        return Err(text);
    }
    
    let json: Value = serde_json::from_str(&text).unwrap_or(Value::String(text));
    Ok(json)
}
