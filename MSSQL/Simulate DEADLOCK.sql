--You can create a deadlock by using the steps shown below. First, create the global temp tables with sample data.
--Two global temp tables with sample data for demo purposes.
CREATE TABLE ##Employees (
    EmpId INT IDENTITY,
     EmpName VARCHAR(16),
     Phone VARCHAR(16))
 GO

INSERT INTO ##Employees (EmpName, Phone)
VALUES ('Martha', '800-555-1212'), ('Jimmy', '619-555-8080')
GO

CREATE TABLE ##Suppliers(
     SupplierId INT IDENTITY,
     SupplierName VARCHAR(64),
     Fax VARCHAR(16))
 GO

INSERT INTO ##Suppliers (SupplierName, Fax)
VALUES ('Acme', '877-555-6060'), ('Rockwell', '800-257-1234')
GO
--Now open two empty query windows in SSMS. Place the code for session 1 in one query window and the code for session 2 in the other query window. 
--Then execute each of the two sessions step by step, going back and forth between the two query windows as required. 
--Note that each transaction has a lock on a resource that the other transaction is also requesting a lock on.

------------------------------------------------------------
Session 1                   | Session 2
------------------------------------------------------------
BEGIN TRAN;                 | BEGIN TRAN;
------------------------------------------------------------
UPDATE ##Employees
SET EmpName = 'Mary'
WHERE empid = 1
------------------------------------------------------------
                            | UPDATE ##Suppliers
                            | SET Fax = N'555-1212'
                            | WHERE supplierid = 1
------------------------------------------------------------
UPDATE ##Suppliers
SET Fax = N'555-1212'
WHERE supplierid = 1
------------------------------------------------------------
<blocked>                    | UPDATE ##Employees
                             | SET phone = N'555-9999'
                             | WHERE empid = 1
------------------------------------------------------------
                             | <blocked>
------------------------------------------------------------

--A deadlock results; one transaction finishes and the other transaction is aborted and error message 1205 is sent to client.
--Close the SSMS query windows for "Session 1" and "Session 2" to commit (or rollback) any open transactions. Lastly, cleanup the temp tables:

DROP TABLE ##Employees
GO
DROP TABLE ##Suppliers
GO