import mysql.connector
from mysql.connector import Error

def create_connection(host_name, user_name, user_password, db_name):
    connection = None
    try:
        connection = mysql.connector.connect(
            host=host_name,
            user=user_name,
            password=user_password,
            database=db_name
        )
        print("Connection to MySQL DB successful")
    except Error as e:
        print(f"The error '{e}' occurred")
    return connection

def execute_query(connection, query):
    cursor = connection.cursor()
    try:
        cursor.execute(query)
        connection.commit()
        print("Query executed successfully")
    except Error as e:
        print(f"The error '{e}' occurred")

def insert_user(connection, driver_id, password, car_brand, car_number, driving_date):
    insert_query = f"""
    INSERT INTO users (driver_id, password, car_brand, car_number, driving_date)
    VALUES ('{driver_id}', '{password}', '{car_brand}', '{car_number}', '{driving_date}');
    """
    execute_query(connection, insert_query)

if __name__ == "__main__":
    # Database connection details
    host = "localhost"
    user = "root"  # Your MySQL username
    password = "nish@2002"  # Your MySQL password
    database = "drowsiness_detection"  # The database you're trying to connect to

    # Create a connection to the database
    conn = create_connection(host, user, password, database)

    # Example of inserting a user
    if conn is not None:
        # Replace these values with actual data you want to insert
        insert_user(conn, 'Driver1', 'password123', 'Toyota', 'ABC123', '2025-04-05')  
        conn.close()  # Close the connection after the operation