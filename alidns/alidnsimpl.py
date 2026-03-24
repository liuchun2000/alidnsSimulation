import json
import uuid
from fastapi import FastAPI, Request, Query
from sqlalchemy import create_engine, Column, String, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import etcd3

# --- 配置 ---
MYSQL_URL = "mysql+pymysql://root:root@127.0.0.1:33306/dns_db"
ETCD_HOST = "localhost"

# --- 数据库模型 ---
Base = declarative_base()


class AliyunDnsRecord(Base):
    __tablename__ = 'aliyun_dns_record'
    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(String(64), unique=True)
    sub_domain = Column(String(128))
    domain_name = Column(String(128))
    public_ip = Column(String(64))
    type = Column(String(10))
    status = Column(String(20))


engine = create_engine(MYSQL_URL)
SessionLocal = sessionmaker(bind=engine)

# --- Etcd 联动逻辑 ---
try:
    etcd = etcd3.client(host=ETCD_HOST)
except Exception as e:
    print(f"Warning: Etcd connection failed: {e}")


def sync_to_etcd(rr: str, domain: str, ip: str, action: str = "PUT"):
    """CoreDNS etcd 插件要求的路径格式: /skydns/com/domain/rr"""
    # 域名反转并补齐末尾的点 (CoreDNS 惯例)
    domain_with_dot = domain if domain.endswith('.') else f"{domain}."
    parts = domain_with_dot.strip('.').split('.')
    reverse_path = "/".join(reversed(parts))
    key = f"/skydns/{reverse_path}/{rr}"

    if action == "PUT":
        # 必须包含 host 字段，CoreDNS 才能解析
        value = json.dumps({"host": ip, "ttl": 600})
        etcd.put(key, value)
    elif action == "DELETE":
        etcd.delete(key)


# --- FastAPI 兼容接口 ---
app = FastAPI()


@app.api_route("/", methods=["GET", "POST"])
async def alidns_gateway(request: Request, Action: str = Query(...)):
    db = SessionLocal()
    # 阿里云 SDK 参数可能在 Query 也可能在 Body，这里统一处理
    params = dict(request.query_params)
    request_id = str(uuid.uuid4()).upper()
    form = await request.form()
    # 将表单数据合并到 params（覆盖 query 中的同名参数）
    for key, value in form.items():
        params[key] = value

    try:
        # 1. AddDomainRecord (对应 SDK: addDomainRecord)
        if Action == "AddDomainRecord":
            rid = str(uuid.uuid4().int)[:14]  # 模拟阿里云的 RecordId
            rr = params.get("RR")
            domain = params.get("DomainName")
            value = params.get("Value")

            new_rec = AliyunDnsRecord(
                record_id=rid, sub_domain=rr, domain_name=domain,
                public_ip=value, type=params.get("Type", "A"), status="Enable"
            )
            db.add(new_rec)
            sync_to_etcd(rr, domain, value)
            db.commit()

            # 返回结构必须完全符合 SDK Model 的字段名 (PascalCase)
            return {"RequestId": request_id, "RecordId": rid}

        # 2. DescribeSubDomainRecords (对应 SDK: describeSubDomainRecords)
        elif Action == "DescribeSubDomainRecords":
            sub_domain_full = params.get("SubDomain", "")
            rr = sub_domain_full.split('.')[0] if '.' in sub_domain_full else sub_domain_full

            records = db.query(AliyunDnsRecord).filter_by(sub_domain=rr).all()

            # SDK 预期结构: Body -> DomainRecords -> Record (List)
            resp_records = []
            for r in records:
                resp_records.append({
                    "RecordId": r.record_id,
                    "RR": r.sub_domain,
                    "Type": r.type,
                    "Value": r.public_ip,
                    "DomainName": r.domain_name,
                    "Status": r.status
                })

            return {
                "RequestId": request_id,
                "TotalCount": len(resp_records),
                "DomainRecords": {"Record": resp_records}
            }

        # 3. DeleteSubDomainRecords (对应 SDK: deleteSubDomainRecords)
        elif Action == "DeleteSubDomainRecords":
            rr = params.get("RR")
            domain = params.get("DomainName")

            db.query(AliyunDnsRecord).filter_by(sub_domain=rr, domain_name=domain).delete()
            sync_to_etcd(rr, domain, "", action="DELETE")
            db.commit()
            return {"RequestId": request_id}

        elif Action == "UpdateDomainRecord":
            rid = params.get("RecordId")
            # 兼容 SDK 的大写参数名
            new_rr = params.get("RR")
            new_value = params.get("Value")

            # 根据 rid 查库，获取域名
            record = db.query(AliyunDnsRecord).filter_by(record_id=rid).first()

            if record:
                # 保存旧的信息用于删除 etcd
                old_rr = record.sub_domain
                domain = record.domain_name  # 从数据库取，不需要 curl 传

                # 2. 删除旧的 Etcd Key
                sync_to_etcd(old_rr, domain, "", action="DELETE")

                # 3. 更新数据库
                record.sub_domain = new_rr
                record.public_ip = new_value

                # 4. 写入新的 Etcd Key (使用数据库里的 domain_name)
                sync_to_etcd(new_rr, domain, new_value, action="PUT")

                db.commit()
                return {"RequestId": request_id, "RecordId": rid}
            else:
                return {"RequestId": request_id, "Message": "RecordId not found"}, 404
        # 5. SetDomainRecordStatus
        elif Action == "SetDomainRecordStatus":
            rid = params.get("RecordId")
            status = params.get("Status")
            record = db.query(AliyunDnsRecord).filter_by(record_id=rid).first()
            if record:
                record.status = status
                if status == "Enable":
                    sync_to_etcd(record.sub_domain, record.domain_name, record.public_ip)
                else:
                    sync_to_etcd(record.sub_domain, record.domain_name, "", action="DELETE")
                db.commit()
            return {"RequestId": request_id}

    except Exception as e:
        print(f"Error: {e}")
        return {"RequestId": request_id, "Message": str(e)}, 500
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)