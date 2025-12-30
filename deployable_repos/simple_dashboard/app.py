"""
Simple Dashboard - DevOps Hero MVP Demo App

A minimal Streamlit dashboard to demonstrate the deployment pipeline.
No external dependencies, no database, just a web UI.
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import altair as alt

# Page config
st.set_page_config(
    page_title="Acme Corp Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for a polished look - warm pastel palette
st.markdown("""
<style>
    .stMetric {
        background: #1f2937;
        padding: 1rem;
        border-radius: 0.75rem;
        border: 1px solid #374151;
    }
    .stMetric label {
        color: #9ca3af !important;
    }
    .stMetric [data-testid="stMetricValue"] {
        color: #f3f4f6 !important;
    }
    .stMetric [data-testid="stMetricDelta"] {
        color: #d1d5db !important;
    }
    div[data-testid="stSidebarContent"] {
        background: linear-gradient(180deg, #292524 0%, #1c1917 100%);
    }
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(90deg, #f59e0b, #d97706);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        color: #a8a29e;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.markdown("## 🏢 ACME Corp")
    st.markdown("---")
    
    st.markdown("### 🎯 Filters")
    
    time_range = st.selectbox(
        "Time Range",
        ["Last 24 hours", "Last 7 days", "Last 30 days", "Last 90 days"],
        index=1
    )
    
    region = st.multiselect(
        "Region",
        ["US East", "US West", "EU", "Asia Pacific"],
        default=["US East", "US West"]
    )
    
    st.markdown("---")
    st.markdown("""
    This dashboard was deployed with **DevOps Hero**.
    
    Deploy internal apps to AWS in minutes—without writing Terraform or opening tickets.
    """)
    
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Main content
st.markdown('<p class="main-header">System Overview</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Real-time metrics for your infrastructure</p>', unsafe_allow_html=True)

# Generate fake but realistic data
np.random.seed(42)

# KPI row
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Requests / sec",
        value="12,847",
        delta="+8.2%",
    )

with col2:
    st.metric(
        label="P99 Latency",
        value="42 ms",
        delta="-12 ms",
    )

with col3:
    st.metric(
        label="Error Rate",
        value="0.03%",
        delta="-0.02%",
    )

with col4:
    st.metric(
        label="Active Users",
        value="3,291",
        delta="+156",
    )

st.markdown("---")

# Charts row
col_left, col_right = st.columns([2, 1])

with col_left:
    st.markdown("### 📈 Traffic Over Time")
    
    # Generate time series data with realistic variance
    dates = pd.date_range(end=datetime.now(), periods=168, freq='h')
    hours = np.arange(168)
    
    # Day/night pattern (peak during work hours)
    daily_pattern = np.sin((hours % 24 - 6) * np.pi / 12).clip(0, 1)
    
    # Weekend dip (every 7th day)
    weekend_factor = np.where((hours // 24) % 7 >= 5, 0.6, 1.0)
    
    # Random spikes and dips
    spikes = np.random.choice([1.0, 1.0, 1.0, 1.3, 1.5, 0.7], 168)
    
    # Base traffic with variance
    api_base = 8000 + daily_pattern * 6000 * weekend_factor * spikes + np.random.normal(0, 1500, 168)
    web_base = 5000 + daily_pattern * 4000 * weekend_factor * spikes + np.random.normal(0, 1000, 168)
    mobile_base = 3000 + daily_pattern * 3000 * weekend_factor * spikes + np.random.normal(0, 800, 168)
    
    traffic_data = pd.DataFrame({
        'timestamp': dates,
        'API': np.maximum(api_base, 500).astype(int),
        'Web': np.maximum(web_base, 300).astype(int),
        'Mobile': np.maximum(mobile_base, 200).astype(int),
    })
    
    # Melt for Altair
    traffic_melted = traffic_data.melt(id_vars=['timestamp'], var_name='Service', value_name='Requests')
    
    # Warm pastel stacked bar chart - amber/orange/cream tones
    bar_chart_time = alt.Chart(traffic_melted).mark_bar(
        opacity=0.9
    ).encode(
        x=alt.X('timestamp:T', title=None, axis=alt.Axis(format='%b %d', labelColor='#a8a29e', tickColor='#44403c', gridColor='#292524')),
        y=alt.Y('Requests:Q', title=None, stack='zero', axis=alt.Axis(labelColor='#a8a29e', tickColor='#44403c', gridColor='#292524')),
        color=alt.Color('Service:N', scale=alt.Scale(domain=['API', 'Web', 'Mobile'], range=['#d97706', '#f59e0b', '#fcd34d']), legend=alt.Legend(orient='bottom', titleColor='#a8a29e', labelColor='#a8a29e')),
        order=alt.Order('Service:N', sort='descending')
    ).properties(
        height=350
    ).configure_view(
        strokeWidth=0
    ).configure(
        background='transparent'
    )
    
    st.altair_chart(bar_chart_time, use_container_width=True)

with col_right:
    st.markdown("### 🌍 Traffic by Region")
    
    region_data = pd.DataFrame({
        'Region': ['US East', 'US West', 'EU', 'Asia Pacific', 'Other'],
        'Requests': [45000, 32000, 28000, 18000, 5000]
    })
    
    # Warm pastel bar chart - amber gradient
    bar_chart = alt.Chart(region_data).mark_bar(
        cornerRadiusTopLeft=4,
        cornerRadiusTopRight=4
    ).encode(
        x=alt.X('Region:N', title=None, sort='-y', axis=alt.Axis(labelColor='#a8a29e', tickColor='#44403c', labelAngle=-45)),
        y=alt.Y('Requests:Q', title=None, axis=alt.Axis(labelColor='#a8a29e', tickColor='#44403c', gridColor='#292524')),
        color=alt.Color('Requests:Q', scale=alt.Scale(range=['#fef3c7', '#d97706']), legend=None)
    ).properties(
        height=350
    ).configure_view(
        strokeWidth=0
    ).configure(
        background='transparent'
    )
    
    st.altair_chart(bar_chart, use_container_width=True)

# Status table
st.markdown("---")
st.markdown("### 🖥️ Service Health")

services = pd.DataFrame({
    'Service': ['api-gateway', 'auth-service', 'user-service', 'payment-service', 'notification-service'],
    'Status': ['🟢 Healthy', '🟢 Healthy', '🟢 Healthy', '🟡 Degraded', '🟢 Healthy'],
    'Uptime': ['99.99%', '99.98%', '99.97%', '98.50%', '99.99%'],
    'Avg Response': ['23ms', '45ms', '38ms', '156ms', '12ms'],
    'Instances': [12, 8, 6, 4, 3],
})

st.dataframe(
    services,
    use_container_width=True,
    hide_index=True,
    column_config={
        'Service': st.column_config.TextColumn('Service', width='medium'),
        'Status': st.column_config.TextColumn('Status', width='small'),
        'Uptime': st.column_config.TextColumn('Uptime', width='small'),
        'Avg Response': st.column_config.TextColumn('Avg Response', width='small'),
        'Instances': st.column_config.NumberColumn('Instances', width='small'),
    }
)

# Footer
st.markdown("---")
col_f1, col_f2, col_f3 = st.columns(3)

with col_f1:
    st.caption("🚀 Deployed with DevOps Hero")
    
with col_f2:
    st.caption(f"📅 {datetime.now().strftime('%B %d, %Y')}")
    
with col_f3:
    st.caption("🔒 Running in your VPC")

