
from fastapi import FastAPI, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import pyodbc
import shutil
import os
import io
import csv
import time

app = FastAPI()

# Enable CORS (open for development; tighten in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Configuration via environment variables ----
DB_SERVER = os.getenv("DB_SERVER", "db")                # 'db' is the Docker service name
DB_PORT = os.getenv("DB_PORT", "1433")
DB_NAME = os.getenv("DB_NAME", "HackathonDB")
DB_USER = os.getenv("DB_USER", "sa")
DB_PASSWORD = os.getenv("DB_PASSWORD", "YourStrong!Passw0rd")
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")  # or "ODBC Driver 18 for SQL Server"

# Build the pyodbc connection string

conn_str = (
    f"DRIVER={{{DB_DRIVER}}};"
    f"SERVER={DB_SERVER},{DB_PORT};"
    f"DATABASE={DB_NAME};"
    f"UID={DB_USER};"
    f"PWD={DB_PASSWORD};"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"  # dev-friendly; in prod use proper certs and set to no
    "Connection Timeout=30;"
)


# Uploads directory (use /home in Linux App Service for persistence)
upload_dir = os.getenv("UPLOAD_DIR", "/home/uploads")
os.makedirs(upload_dir, exist_ok=True)

# ---- Helpers ----
def wait_for_db(max_attempts: int = 20, delay_seconds: int = 3):
    """Retry connecting to the DB until it's reachable (helps when SQL container needs time to start)."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            conn = pyodbc.connect(conn_str)
            conn.close()
            print(f"[DB] Connection successful (attempt {attempt})")
            return
        except Exception as ex:
            last_error = ex
            print(f"[DB] Not ready yet (attempt {attempt}/{max_attempts}): {ex}")
            time.sleep(delay_seconds)
    raise RuntimeError(f"Database not reachable after {max_attempts} attempts. Last error: {last_error}")

# ✅ Initialize DB tables
def init_db():
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("""
    IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Employees' AND xtype='U')
    CREATE TABLE Employees (
        EmployeeID INT PRIMARY KEY IDENTITY(1,1),
        Name NVARCHAR(100),
        Email NVARCHAR(100),
        Department NVARCHAR(50),
        ManagerID NVARCHAR(50)
    )
    """)
    cursor.execute("""
    IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Expenses' AND xtype='U')
    CREATE TABLE Expenses (
        ExpenseID INT PRIMARY KEY IDENTITY(1,1),
        EmployeeID INT FOREIGN KEY REFERENCES Employees(EmployeeID),
        Amount DECIMAL(10,2),
        Category NVARCHAR(50),
        ExpenseDate DATE,
        Description NVARCHAR(255),
        ReceiptPath NVARCHAR(255),
        Status NVARCHAR(20) DEFAULT 'Pending',
        SubmittedOn DATETIME DEFAULT GETDATE()
    )
    """)
    conn.commit()
    conn.close()

# Wait for DB and create tables at startup
wait_for_db()
init_db()



# ✅ Submit Expense
@app.post("/submit-expense")
async def submit_expense(
    employee_id: int = Form(...),
    amount: float = Form(...),
    category: str = Form(...),
    expense_date: str = Form(...),
    description: str = Form(...),
    receipt: UploadFile = None
):
    file_path = None
    if receipt:
        file_path = os.path.join(upload_dir, receipt.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(receipt.file, buffer)

    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO Expenses (EmployeeID, Amount, Category, ExpenseDate, Description, ReceiptPath)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (employee_id, amount, category, expense_date, description, file_path))
    conn.commit()
    conn.close()
    return JSONResponse({"message": "Expense submitted successfully!"})

# ✅ Employees list
@app.get("/employees")
async def get_employees():
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("SELECT EmployeeID, Name FROM Employees")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row.EmployeeID, "name": row.Name} for row in rows]

# ✅ Managers list
@app.get("/managers")
async def get_managers():
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ManagerID FROM Employees")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row.ManagerID, "name": row.ManagerID} for row in rows]

# ✅ Pending Approvals
@app.get("/pending-approvals/{manager_id}")
async def pending_approvals(manager_id: str):
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.ExpenseID, e.ExpenseDate, e.Amount, e.Category, e.Status, emp.Name
        FROM Expenses e
        JOIN Employees emp ON e.EmployeeID = emp.EmployeeID
        WHERE e.Status='Pending' AND emp.ManagerID=?
    """, manager_id)
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": row.ExpenseID, "date": str(row.ExpenseDate), "amount": float(row.Amount),
         "category": row.Category, "status": row.Status, "employeeName": row.Name}
        for row in rows
    ]

# ✅ Approve / Reject
@app.put("/approve/{expense_id}")
async def approve_expense(expense_id: int):
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("UPDATE Expenses SET Status='Approved' WHERE ExpenseID=?", expense_id)
    conn.commit()
    conn.close()
    return {"message": "Expense approved"}

@app.put("/reject/{expense_id}")
async def reject_expense(expense_id: int):
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("UPDATE Expenses SET Status='Rejected' WHERE ExpenseID=?", expense_id)
    conn.commit()
    conn.close()
    return {"message": "Expense rejected"}

# ✅ Expense History
@app.get("/expense-history/{employee_id}")
async def expense_history(employee_id: int):
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ExpenseDate, Category, Amount, Status, Description
        FROM Expenses WHERE EmployeeID=? ORDER BY ExpenseDate DESC
    """, employee_id)
    rows = cursor.fetchall()
    conn.close()
    return [
        {"date": str(row.ExpenseDate), "category": row.Category, "amount": float(row.Amount),
         "status": row.Status, "description": row.Description}
        for row in rows
    ]

@app.get("/dashboard-summary")
async def dashboard_summary(filter_type: str = None, id: str = None):
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()

    # ✅ Total Approved Expenses
    if filter_type == "manager":
        cursor.execute("""
            SELECT ISNULL(SUM(e.Amount), 0) FROM Expenses e
            JOIN Employees emp ON e.EmployeeID = emp.EmployeeID
            WHERE emp.ManagerID=? AND e.Status='Approved'
        """, id)
    elif filter_type == "employee":
        cursor.execute("""
            SELECT ISNULL(SUM(Amount), 0) FROM Expenses
            WHERE EmployeeID=? AND Status='Approved'
        """, id)
    else:
        cursor.execute("SELECT ISNULL(SUM(Amount), 0) FROM Expenses WHERE Status='Approved'")
    total_expenses = cursor.fetchone()[0]

    # ✅ Pending count (still show pending for reference)
    cursor.execute("SELECT COUNT(*) FROM Expenses WHERE Status='Pending'")
    pending_count = cursor.fetchone()[0]

    # ✅ Approved Category Breakdown
    if filter_type == "manager":
        cursor.execute("""
            SELECT Category, SUM(e.Amount) FROM Expenses e
            JOIN Employees emp ON e.EmployeeID = emp.EmployeeID
            WHERE emp.ManagerID=? AND e.Status='Approved'
            GROUP BY Category
        """, id)
    elif filter_type == "employee":
        cursor.execute("""
            SELECT Category, SUM(Amount) FROM Expenses
            WHERE EmployeeID=? AND Status='Approved'
            GROUP BY Category
        """, id)
    else:
        cursor.execute("""
            SELECT Category, SUM(Amount) FROM Expenses
            WHERE Status='Approved'
            GROUP BY Category
        """)
    categories = [{"category": row[0], "total": float(row[1])} for row in cursor.fetchall()]

    conn.close()
    return {
        "total_expenses": float(total_expenses),
        "pending_count": pending_count,
        "categories": categories
    }


@app.get("/monthly-expense-trend")
async def monthly_expense_trend(filter_type: str = None, id: str = None):
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()

    if filter_type == "manager":
        cursor.execute("""
            SELECT FORMAT(e.ExpenseDate, 'yyyy-MM'), SUM(e.Amount)
            FROM Expenses e
            JOIN Employees emp ON e.EmployeeID = emp.EmployeeID
            WHERE emp.ManagerID=? AND e.Status='Approved'
            GROUP BY FORMAT(e.ExpenseDate, 'yyyy-MM')
            ORDER BY 1
        """, id)
    elif filter_type == "employee":
        cursor.execute("""
            SELECT FORMAT(ExpenseDate, 'yyyy-MM'), SUM(Amount)
            FROM Expenses
            WHERE EmployeeID=? AND Status='Approved'
            GROUP BY FORMAT(ExpenseDate, 'yyyy-MM')
            ORDER BY 1
        """, id)
    else:
        cursor.execute("""
            SELECT FORMAT(ExpenseDate, 'yyyy-MM'), SUM(Amount)
            FROM Expenses
            WHERE Status='Approved'
            GROUP BY FORMAT(ExpenseDate, 'yyyy-MM')
            ORDER BY 1
        """)
    rows = cursor.fetchall()
    conn.close()
    return [{"month": row[0], "total": float(row[1])} for row in rows]


