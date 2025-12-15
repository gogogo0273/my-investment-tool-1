import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- 設定頁面 ---
st.set_page_config(page_title="我的自動化投資儀表板", layout="wide")

# --- 連接 Google Sheets 的函數 (使用快取避免重複連線) ---
@st.cache_resource
def connect_to_gsheets():
    # 定義權限範圍
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # 從 Streamlit Secrets 讀取金鑰 (這樣最安全，不用把密碼檔放上網)
    # 我們稍後會在 Step 3 教你怎麼設定這個 secrets
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"連線失敗，請檢查 Secrets 設定: {e}")
        return None

def main():
    st.title("💰 我的投資戰情室")
    st.markdown("---")

    # 1. 建立連線
    client = connect_to_gsheets()
    if not client:
        st.stop() # 如果連線失敗就停止執行

    # 設定您的 Google Sheet 網址 (請換成您的網址，或是放在 secrets 裡)
    # 這裡示範從 secrets 讀取，或者您也可以直接寫在程式碼裡: 
    # sheet_url = "https://docs.google.com/spreadsheets/d/您的ID/edit"
    try:
        sheet_url = st.secrets["private_gsheets_url"]
        sh = client.open_by_url(sheet_url)
    except Exception as e:
        st.error("無法開啟試算表，請確認網址正確且權限已開給 Service Account。")
        st.stop()

    # --- 側邊欄功能 ---
    st.sidebar.header("功能選單")
    menu = st.sidebar.radio("請選擇", ["資產總覽 (Dashboard)", "個別基金明細", "📝 新增交易"])

    # ==========================================
    # 功能 1: 資產總覽 (讀取 '總和' 分頁)
    # ==========================================
    if menu == "資產總覽 (Dashboard)":
        if st.button("🔄 重新整理 (抓取最新淨值)"):
            st.cache_data.clear()
        
        try:
            # 讀取 "總和" 分頁
            ws = sh.worksheet("總和")
            data = ws.get_all_records() # 讀取所有資料
            df_summary = pd.DataFrame(data)

            # 資料清理：確保數值欄位是數字
            # 根據您的 CSV，欄位可能有：'總現值\n含息', '損益\n(含息)' 等
            # 我們用模糊搜尋來找欄位
            try:
                # 尋找關鍵欄位名稱
                val_col = [c for c in df_summary.columns if "總現值" in c and "含息" in c][0]
                profit_col = [c for c in df_summary.columns if "損益" in c and "含息" in c][0]
                name_col = [c for c in df_summary.columns if "基金名稱" in c][0]
                
                # 轉換為數值 (去除錢字號或逗號)
                for col in [val_col, profit_col]:
                    if df_summary[col].dtype == 'object':
                        df_summary[col] = df_summary[col].astype(str).str.replace(r'[$,]', '', regex=True)
                        df_summary[col] = pd.to_numeric(df_summary[col], errors='coerce')
                
                # 計算總和
                total_assets = df_summary[val_col].sum()
                total_profit = df_summary[profit_col].sum()

                # --- 頂部指標 ---
                col1, col2 = st.columns(2)
                col1.metric("總資產 (含息)", f"${total_assets:,.2f}")
                col2.metric("總損益 (含息)", f"${total_profit:,.2f}", 
                            delta=f"{total_profit:,.2f}")

                # --- 圖表區 ---
                c1, c2 = st.columns([1, 1])
                with c1:
                    st.subheader("資產配置 (圓餅圖)")
                    fig_pie = px.pie(df_summary, values=val_col, names=name_col, hole=0.4)
                    st.plotly_chart(fig_pie, use_container_width=True)
                
                with c2:
                    st.subheader("各基金損益 (長條圖)")
                    # 依損益排序
                    df_sorted = df_summary.sort_values(by=profit_col, ascending=False)
                    fig_bar = px.bar(df_sorted, x=name_col, y=profit_col, 
                                     color=profit_col, color_continuous_scale="RdYlGn")
                    st.plotly_chart(fig_bar, use_container_width=True)

                st.subheader("詳細數據")
                st.dataframe(df_summary)

            except Exception as e:
                st.warning(f"欄位解析錯誤，請檢查 Google Sheet '總和' 分頁的標題名稱是否變動。錯誤: {e}")
                st.write("讀取到的原始資料:", df_summary.head())

        except Exception as e:
            st.error(f"讀取 '總和' 分頁失敗: {e}")

# ==========================================
    # 功能 2: 個別基金明細 (已修正重複欄位錯誤)
    # ==========================================
    elif menu == "個別基金明細":
        # 排除非基金的分頁
        ignore_sheets = ["總和", "配息", "工作表1", "Lists", "Dropdowns"] 
        # 確保只讀取真正存在的 sheet
        all_sheets = [s.title for s in sh.worksheets() if s.title not in ignore_sheets]
        
        selected_fund = st.selectbox("選擇基金", all_sheets)
        
        if selected_fund:
            try:
                ws = sh.worksheet(selected_fund)
                data = ws.get_all_values()
                
                if len(data) > 0:
                    raw_headers = data[0] # 原始標題
                    rows = data[1:]       # 數據內容
                    
                    # --- 關鍵修正：處理重複或空白的標題 ---
                    final_headers = []
                    header_count = {}

                    for i, col_name in enumerate(raw_headers):
                        # 1. 處理空白標題
                        col_name = col_name.strip()
                        if not col_name:
                            col_name = f"空欄_{i}" 
                        
                        # 2. 處理重複標題 (例如有兩個 '備註')
                        if col_name in header_count:
                            header_count[col_name] += 1
                            new_name = f"{col_name}_{header_count[col_name]}"
                        else:
                            header_count[col_name] = 0
                            new_name = col_name
                        
                        final_headers.append(new_name)
                    # ------------------------------------

                    # 使用處理過的唯一標題建立 DataFrame
                    df = pd.DataFrame(rows, columns=final_headers)
                    
                    st.subheader(f"📂 {selected_fund} 交易紀錄")
                    st.dataframe(df, use_container_width=True)
                else:
                    st.warning("此分頁沒有資料。")

            except Exception as e:
                st.error(f"讀取分頁錯誤: {e}")

    # ==========================================
    # 功能 3: 新增交易 (寫入 Google Sheets)
    # ==========================================
    elif menu == "📝 新增交易":
        st.header("新增一筆交易")
        
        # 1. 選擇要寫入哪個分頁
        ignore_sheets = ["總和", "配息", "工作表1"] 
        all_sheets = [s.title for s in sh.worksheets() if s.title not in ignore_sheets]
        target_fund = st.selectbox("選擇基金 (寫入目標)", all_sheets)
        
        # 2. 輸入表單
        with st.form("add_transaction"):
            col1, col2 = st.columns(2)
            with col1:
                date_val = st.date_input("交易日期", datetime.today())
                trans_type = st.selectbox("交易類別", ["買入", "賣出", "配息再投資", "轉換入", "轉換出"])
                price = st.number_input("成交淨值/單價", min_value=0.0, format="%.4f")
            
            with col2:
                amount = st.number_input("交易總金額 (USD/TWD/ZAR)", min_value=0.0, format="%.2f")
                fee = st.number_input("手續費", min_value=0.0, format="%.2f", value=0.0)
            
            # 自動計算預估單位數 (僅供參考，寫入時還是會寫進去)
            est_units = 0.0
            if price > 0:
                est_units = (amount - fee) / price if trans_type == "買入" else amount / price
            
            st.info(f"預估單位數: {est_units:,.4f}")
            
            submit_btn = st.form_submit_button("確認新增")

        # 3. 處理寫入邏輯
        if submit_btn:
            try:
                ws = sh.worksheet(target_fund)
                
                # --- 建構要寫入的列 ---
                # 注意：這裡的順序必須跟您 Excel/Google Sheet 的欄位順序一模一樣！
                # 根據您的 CSV 範例 (Fund-Bond.xlsx - 00878.csv):
                # 日期(A), 入帳日(B), 類別(C), 金額(D), 價格(E), 匯率(F), 手續費(G), 空(H), 空(I), 單位數(J)
                
                new_row = [
                    str(date_val),  # A: 日期
                    "",             # B: 實際入帳日 (留空)
                    trans_type,     # C: 類別
                    amount,         # D: 金額
                    price,          # E: 價格
                    "",             # F: 匯率 (視需求填寫)
                    fee,            # G: 手續費
                    "",             # H
                    "",             # I
                    est_units       # J: 單位數
                ]
                
                # 寫入到最後一列
                ws.append_row(new_row)
                
                st.success(f"✅ 成功寫入 {target_fund}！請重新整理頁面查看。")
                st.balloons()
                
            except Exception as e:
                st.error(f"寫入失敗: {e}")

if __name__ == "__main__":
    main()