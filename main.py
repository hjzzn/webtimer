import os
import ssl
import json
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from apscheduler.schedulers.background import BackgroundScheduler
import random

try:
    import paho.mqtt.client as mqtt

    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

# --- 1. SQLite 数据库配置 ---
DB_PATH = os.path.join(os.path.dirname(__file__), "tasks.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class TaskModel(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    time_value = Column(String, nullable=False)
    device_name = Column(String, nullable=False)
    command = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class DeviceModel(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    uuid = Column(String, nullable=False, unique=True)
    device_type = Column(String, nullable=False)
    location = Column(String, nullable=False)
    status = Column(String, nullable=False)
    data = Column(String, nullable=True)


Base.metadata.create_all(bind=engine)

# --- 2. MQTT 配置 ---
mqtt_client = None
MQTT_BROKER = "u1617077.ala.asia-southeast1.emqxsl.com"
MQTT_PORT = 8883
MQTT_USER = "hjzzn"
MQTT_PASS = "netzzn"

random_suffix = random.randint(100, 999)
MY_CLIENT_ID = f"Home_IoT_Gateway_Backend_{random_suffix}"
CA_CERT_NAME = "emqxsl-ca.crt"

# 🌍 流量超级优化核心：动态感知当前有哪个传感器页面被用户打开了
# 数据结构为 { "sensor_uuid": 剩余保持轮询寿命秒数 }
active_sensors = {}


def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        print("🟢 [MQTT 安全通道] 成功连接至云端 EMQX 服务器！")
        client.subscribe("/SENSOR/+/DATA", qos=1)
        print("📡 [MQTT 安全通道] 已成功订阅传感器数据监听主题: /SENSOR/+/DATA")
    else:
        print(f"❌ [MQTT 安全通道] 登录失败，错误码: {rc}")


def on_mqtt_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload_str = msg.payload.decode('utf-8')
        if topic.startswith("/SENSOR/") and topic.endswith("/DATA"):
            parts = topic.split('/')
            if len(parts) >= 4:
                uuid_val = parts[2]
                db = SessionLocal()
                device = db.query(DeviceModel).filter(DeviceModel.uuid == uuid_val).first()
                if device:
                    device.data = payload_str
                    device.status = "在线"
                    db.commit()
                    print(f"📥 [MQTT 接收] 传感器 [{device.name}] 上报新数据: {payload_str}")
                db.close()
    except Exception as e:
        print(f"❌ 解析 MQTT 接收消息异常: {e}")


def sendCommandToMqtt(topic: str, payload_str: str):
    global mqtt_client
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if MQTT_AVAILABLE and mqtt_client and mqtt_client.is_connected():
        try:
            info = mqtt_client.publish(topic, payload_str, qos=1)
            info.wait_for_publish()
            print(f"[{now_str}] 🚀 外发 -> 主题: {topic} | 内容: {payload_str}")
        except Exception as e:
            print(f"❌ 发送异常: {e}")
    else:
        print(f"[{now_str}] ⚠️ 发送失败，MQTT 处于断开状态")


# --- 3. APScheduler 定时任务引擎 ---
scheduler = BackgroundScheduler()


# 【按需轮询核心逻辑】：每 3 秒执行一次，但只对当前被前端激活的传感器发数据！
def poll_active_sensors_3s():
    global active_sensors

    # 衰减并清理过期的传感器激活寿命
    expired_uuids = []
    for uuid in list(active_sensors.keys()):
        active_sensors[uuid] -= 3
        if active_sensors[uuid] <= 0:
            expired_uuids.append(uuid)

    for uuid in expired_uuids:
        del active_sensors[uuid]
        print(f"🛑 [按需流量控制] 前端页面已关闭，彻底停止对传感器 UUID: {uuid} 的轮询读数据消息！")

    # 只对目前处于激活状态（用户正在网页上看）的传感器下发读命令
    if active_sensors:
        print(f"📊 [按需流量控制] 检测到有 {len(active_sensors)} 个传感器页面处于打开状态，正在下发轮询...")
        for uuid in active_sensors.keys():
            read_topic = f"/SENSOR/{uuid}/READ"
            sendCommandToMqtt(read_topic, "READ")


def execute_task(task_name: str, device_name: str, command: str):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == device_name).first()
    db.close()
    if device:
        if device.device_type == "电脑" and command in ["ON", "FORCE_OFF"]:
            dynamic_topic = f"SWITCH/{device.uuid}/POWER"
            sendCommandToMqtt(dynamic_topic, command)
        elif device.device_type == "开关器" and command in ["ON", "OFF"]:
            dynamic_topic = f"SWITCH/{device.uuid}/POWER"
            sendCommandToMqtt(dynamic_topic, command)


def execute_countdown_task(device_name: str, command: str):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == device_name).first()
    db.close()
    if device:
        if (device.device_type == "电脑" and command in ["ON", "FORCE_OFF"]) or \
                (device.device_type == "开关器" and command in ["ON", "OFF"]):
            dynamic_topic = f"SWITCH/{device.uuid}/POWER"
            sendCommandToMqtt(dynamic_topic, command)


def add_task_to_scheduler(task_id: int, name: str, task_type: str, time_value: str, device_name: str, command: str,
                          is_active: bool):
    job_id = str(task_id)
    if scheduler.get_job(job_id): scheduler.remove_job(job_id)
    if not is_active: return
    try:
        if task_type == 'once':
            run_time = datetime.strptime(time_value, '%Y-%m-%d %H:%M')
            scheduler.add_job(execute_task, 'date', run_date=run_time, args=[name, device_name, command], id=job_id)
        elif task_type == 'minute':
            scheduler.add_job(execute_task, 'interval', minutes=int(time_value), args=[name, device_name, command],
                              id=job_id)
        elif task_type == 'daily':
            hour, minute = map(int, time_value.split(':'))
            scheduler.add_job(execute_task, 'cron', hour=hour, minute=minute, args=[name, device_name, command],
                              id=job_id)
    except Exception as e:
        print(f"❌ 加载定时任务失败 [{name}]: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mqtt_client
    if MQTT_AVAILABLE:
        try:
            mqtt_client = mqtt.Client(client_id=MY_CLIENT_ID, clean_session=True)
            mqtt_client.on_connect = on_mqtt_connect
            mqtt_client.on_message = on_mqtt_message
            mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

            base_dir = os.path.dirname(__file__)
            cert_path = os.path.join(base_dir, CA_CERT_NAME)
            if os.path.exists(cert_path):
                mqtt_client.tls_set(ca_certs=cert_path, tls_version=ssl.PROTOCOL_TLSv1_2)
            else:
                mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)

            mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
            mqtt_client.loop_start()
        except Exception as e:
            print(f"⚠️ 启动 MQTT 失败: {e}")

    # 挂载流量控制轮询任务
    scheduler.add_job(poll_active_sensors_3s, 'interval', seconds=3, id="sensor_dynamic_polling_job")
    scheduler.start()

    db = SessionLocal()
    tasks = db.query(TaskModel).all()
    for task in tasks:
        add_task_to_scheduler(task.id, task.name, task.task_type, task.time_value, task.device_name, task.command,
                              task.is_active)
    db.close()
    yield
    scheduler.shutdown()
    if MQTT_AVAILABLE and mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


app = FastAPI(title="智能设备核心集成系统", lifespan=lifespan)


class DeviceCreate(BaseModel):
    name: str
    uuid: str
    device_type: str
    location: str
    status: str
    data: str


class ImmediateControl(BaseModel):
    command: str


@app.get("/api/devices")
def get_devices():
    db = SessionLocal()
    devices = db.query(DeviceModel).all()
    db.close()
    return devices


@app.get("/api/devices/{name}")
def get_device_by_name(name: str):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == name).first()
    db.close()
    if not device: raise HTTPException(status_code=404, detail="设备不存在")
    return device


# 【新增 API 路由】：供传感器页面维持唤醒心跳
@app.post("/api/devices/sensor-ping/{uuid}")
def receive_sensor_page_ping(uuid: str):
    global active_sensors
    # 只要收到前端心跳，给这个 UUID 续命 6 秒（能支撑 2 次后端轮询周期）
    active_sensors[uuid] = 6
    return {"status": "active", "uuid": uuid}


@app.post("/api/devices")
def create_device(device: DeviceCreate):
    db = SessionLocal()
    existing = db.query(DeviceModel).filter(
        (DeviceModel.name == device.name) | (DeviceModel.uuid == device.uuid)).first()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail="设备名称或 UUID 已存在")
    db_device = DeviceModel(name=device.name, uuid=device.uuid, device_type=device.device_type,
                            location=device.location, status=device.status, data=device.data)
    db.add(db_device)
    db.commit()
    db.close()
    return {"message": "设备添加成功"}


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: int):
    db = SessionLocal()
    dev = db.query(DeviceModel).filter(DeviceModel.id == device_id).first()
    if dev:
        db.query(TaskModel).filter(TaskModel.device_name == dev.name).delete()
        db.delete(dev)
        db.commit()
    db.close()
    return {"message": "设备删除成功"}


@app.post("/api/devices/{name}/control")
def immediate_control_device(name: str, payload: ImmediateControl):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == name).first()
    if not device:
        db.close()
        raise HTTPException(status_code=404, detail="设备不存在")
    if device.device_type == "电脑":
        if payload.command not in ["ON", "FORCE_OFF"]:
            db.close()
            raise HTTPException(status_code=400, detail="电脑仅接受 ON 或 FORCE_OFF 指令")
    elif device.device_type == "开关器":
        if payload.command not in ["ON", "OFF"]:
            db.close()
            raise HTTPException(status_code=400, detail="开关器仅接受 ON 或 OFF 指令")
    else:
        db.close()
        raise HTTPException(status_code=400, detail="该设备类型为传感器，不支持反向控制指令")

    device_uuid = device.uuid
    device.data = payload.command
    db.commit()
    db.close()

    dynamic_topic = f"SWITCH/{device_uuid}/POWER"
    sendCommandToMqtt(dynamic_topic, payload.command)
    return {"message": f"设备已即时切换"}


@app.get("/api/tasks")
def get_tasks(device_name: str = None):
    db = SessionLocal()
    tasks = db.query(TaskModel).filter(TaskModel.device_name == device_name).all() if device_name else db.query(
        TaskModel).all()
    db.close()
    return tasks


# --- 静态路由映射 ---
@app.get("/")
def read_index(): return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/detail")
def read_detail(): return FileResponse(os.path.join(os.path.dirname(__file__), "static", "detail.html"))


@app.get("/sensor")
def read_sensor(): return FileResponse(os.path.join(os.path.dirname(__file__), "static", "sensor.html"))


@app.get("/query")
def read_query(): return FileResponse(os.path.join(os.path.dirname(__file__), "static", "query.html"))


if __name__ == '__main__':
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)